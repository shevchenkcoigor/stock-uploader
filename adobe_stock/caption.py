"""Generate Adobe Stock titles and keywords with headless Claude Code (no API key needed).

Each call receives several downscaled images and returns JSON. The call is slimmed down
(no MCP servers, no skills, no user settings, short system prompt) so it uses a few thousand
tokens instead of the ~150k a default Claude Code session loads.
"""

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from adobe_stock.categories import CATEGORIES, category_menu
from adobe_stock.metadata_csv import MAX_KEYWORDS, MAX_TITLE_CHARS

DEFAULT_MODEL = "claude-haiku-5-5"
# (model id, short note shown in the picker); extend this list when new models appear.
MODELS = (
    ("claude-haiku-5-5", "fastest and lightest on usage limits (default, good for bulk)"),
    ("claude-sonnet-5-5", "more accurate, uses limits faster"),
    ("claude-opus-5-5", "stronger still, much heavier on limits"),
    ("claude-fable-5-2", "most capable, heaviest on limits"),
)
MODEL_IDS = tuple(model_id for model_id, _ in MODELS)
PREVIEW_MAX_SIDE = 1568
PREVIEW_QUALITY = 85
CALL_TIMEOUT_SECONDS = 300
ERROR_TAIL_CHARS = 300
# Labels Adobe adds itself through the AI flag; keeping them out of keywords.
BANNED_KEYWORDS = frozenset(
    {"ai", "ai generated", "ai-generated", "generative ai", "artificial intelligence",
     "generated", "midjourney", "dall-e", "stable diffusion"}
)

SYSTEM_PROMPT = (
    "You write metadata for Adobe Stock. Open each listed image with the Read tool and answer "
    "only with the requested JSON.\n"
    "For every image:\n"
    "- title: a factual English description, 6-15 words, under 150 characters. Describe only "
    "what is visible. No brand names, trademarks, people's names or quotation marks.\n"
    "- keywords: 25-35 English keywords (single words or short phrases), most relevant first, "
    "covering subject, setting, colors, mood, composition and likely uses. Only things visible "
    "or clearly implied. No brands, trademarks, artist names, and no words like 'AI' or "
    "'generated'.\n"
    "- category: the single best-fitting category code from this list: " + category_menu() + "."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "title": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "category": {"type": "integer", "enum": list(CATEGORIES)},
                },
                "required": ["file", "title", "keywords", "category"],
            },
        }
    },
    "required": ["items"],
}


class ClaudeCallError(RuntimeError):
    """The claude process failed as a whole (limits, auth, timeout), not for one image."""


@dataclass(frozen=True)
class CaptionResult:
    path: Path
    title: str = ""
    keywords: tuple[str, ...] = ()
    category: str = ""
    error: str | None = None


def make_preview(source: Path, destination: Path) -> None:
    """Write a small JPEG copy; originals can be 100 MP, far more than needed to describe them."""
    with Image.open(source) as image:
        image.draft("RGB", (PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))  # fast JPEG downscale on decode
        image = image.convert("RGB")
        image.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
        image.save(destination, "JPEG", quality=PREVIEW_QUALITY)


def clean_title(raw: str) -> str:
    title = " ".join(raw.replace('"', "").split())
    if len(title) <= MAX_TITLE_CHARS:
        return title
    return title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0]


def clean_keywords(raw: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    kept: list[str] = []
    for keyword in raw:
        normalized = " ".join(keyword.split()).lower()
        if not normalized or normalized in seen or normalized in BANNED_KEYWORDS:
            continue
        seen.add(normalized)
        kept.append(normalized)
    return tuple(kept[:MAX_KEYWORDS])


def _category_code(raw: object) -> str:
    """The category as a CSV code string; anything outside Adobe's list is dropped (optional field)."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw in CATEGORIES:
        return str(raw)
    return ""


def build_command(preview_dir: Path, names: list[str], model: str) -> list[str]:
    prompt = (
        "Describe each of these images (files in the current directory):\n"
        + "\n".join(names)
        + "\nReturn one item per image; 'file' must be the exact file name listed."
    )
    return [
        "claude", "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--tools", "Read",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources", "",
        "--no-session-persistence",
        "--system-prompt", SYSTEM_PROMPT,
        "--add-dir", str(preview_dir),
        "--json-schema", json.dumps(RESPONSE_SCHEMA),
    ]


def run_claude(command: list[str], cwd: Path) -> str:
    """Run the CLI and return stdout; raise ClaudeCallError if the call as a whole failed."""
    try:
        completed = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=CALL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise ClaudeCallError(f"claude did not run: {error}") from error
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout)[-ERROR_TAIL_CHARS:]
        raise ClaudeCallError(f"claude exited with {completed.returncode}: {tail}")
    return completed.stdout


def parse_items(stdout: str) -> dict[str, dict]:
    """Extract {file: {title, keywords}} from the CLI's JSON envelope."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ClaudeCallError(f"output is not JSON: {stdout[:ERROR_TAIL_CHARS]}") from error
    if envelope.get("is_error"):
        raise ClaudeCallError(f"claude reported an error: {str(envelope.get('result'))[:ERROR_TAIL_CHARS]}")
    payload = envelope.get("structured_output")
    if payload is None:
        try:
            payload = json.loads(envelope.get("result", ""))
        except (json.JSONDecodeError, TypeError) as error:
            raise ClaudeCallError("no structured output in the response") from error
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ClaudeCallError("response has no 'items' list")
    return {item["file"]: item for item in items if isinstance(item, dict) and "file" in item}


def caption_group(sources: list[Path], model: str = DEFAULT_MODEL) -> list[CaptionResult]:
    """Caption several images in one call. Per-image problems are returned as errors."""
    with tempfile.TemporaryDirectory(prefix="adobe_caption_") as tmp:
        preview_dir = Path(tmp)
        names: dict[str, Path] = {}
        for index, source in enumerate(sources, start=1):
            name = f"img{index:03d}.jpg"
            make_preview(source, preview_dir / name)
            names[name] = source
        stdout = run_claude(build_command(preview_dir, list(names), model), preview_dir)
        items = parse_items(stdout)

    results = []
    for name, source in names.items():
        item = items.get(name)
        if item is None:
            results.append(CaptionResult(source, error="model returned nothing for this image"))
            continue
        title = clean_title(str(item.get("title", "")))
        keywords = clean_keywords([str(k) for k in item.get("keywords", [])])
        if not title or not keywords:
            results.append(CaptionResult(source, error="empty title or keywords"))
            continue
        results.append(CaptionResult(source, title, keywords, _category_code(item.get("category"))))
    return results
