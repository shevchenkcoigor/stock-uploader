"""Generate Adobe Stock titles and keywords with headless Claude Code (no API key needed).

Each call receives several downscaled images and returns JSON. The call is slimmed down
(no MCP servers, no skills, no user settings, short system prompt) so it uses a few thousand
tokens instead of the ~150k a default Claude Code session loads.
"""

import glob
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from adobe_stock.categories import CATEGORIES, category_menu
from adobe_stock.metadata_csv import (
    AI_TERM_PATTERN,
    MAX_KEYWORDS,
    MAX_TITLE_CHARS,
    keyword_problem,
)

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
SYSTEM_PROMPT = (
    "You write metadata for Adobe Stock. Open each listed image with the Read tool and answer "
    "only with the requested JSON.\n"
    "For every image:\n"
    "- title: a factual English description, 6-15 words, under 150 characters. Describe only "
    "what is visible. No brand names, trademarks, people's names or quotation marks. Never "
    "mention that the image is AI-made or name AI tools: the words 'AI', 'generative', "
    "'generated', 'artificial intelligence', 'Midjourney', 'DALL-E', 'Stable Diffusion', "
    "'ChatGPT' and 'Firefly' must not appear in the title or in the keywords; the tool adds "
    "the required 'Generative AI' label to the title itself.\n"
    "- keywords: 25-35 English keywords (single words or short phrases), most relevant first, "
    "covering subject, setting, colors, mood, composition and likely uses. Only things visible "
    "or clearly implied. No brands, trademarks, artist names, and no words like 'AI' or "
    "'generated'. Each keyword is ONE concept: a single word, or a short phrase only when it "
    "names one thing (like 'arctic fox'); never join several concepts into one keyword and never "
    "put a comma or semicolon inside a keyword. Put the main subject first and the individual "
    "words and concepts of your title within the first 10 keywords. No numbers and no file or "
    "camera information. If a keyword names a city, state or province, add the country as a "
    "separate keyword. Use each keyword once.\n"
    "- category: the single best-fitting category code from this list: " + category_menu() + ".\n"
    "- people_visible: true if any person or human-like figure is visible.\n"
    "- content_type: 'photo' if it looks camera-captured and photo-realistic, otherwise "
    "'illustration'.\n"
    "- review_reason: Adobe's generative-AI policy forbids names of artists, real people or "
    "fictional characters, references to copyrighted works, government agencies, third-party "
    "intellectual property (brands, logos, trademarks) and anything implying a real newsworthy "
    "event. Never put any of these in the title or keywords; describe such things generically. "
    "If the image itself shows or strongly resembles any of them, or a recognizable real person, "
    "explain briefly in review_reason; otherwise use an empty string."
)

CONTENT_TYPES = ("photo", "illustration")

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
                    "people_visible": {"type": "boolean"},
                    "content_type": {"type": "string", "enum": list(CONTENT_TYPES)},
                    "review_reason": {"type": "string"},
                },
                "required": [
                    "file",
                    "title",
                    "keywords",
                    "category",
                    "people_visible",
                    "content_type",
                    "review_reason",
                ],
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
    people_visible: bool = False
    content_type: str = ""
    review_reason: str = ""  # set when the image may break Adobe's AI policy; needs a human
    error: str | None = None


def make_preview(source: Path, destination: Path) -> None:
    """Write a small JPEG copy; originals can be 100 MP, far more than needed to describe them."""
    with Image.open(source) as image:
        image.draft("RGB", (PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))  # fast JPEG downscale on decode
        image = image.convert("RGB")
        image.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
        image.save(destination, "JPEG", quality=PREVIEW_QUALITY)


def clean_title(raw: str) -> str:
    title = " ".join(AI_TERM_PATTERN.sub(" ", raw.replace('"', "")).split())
    title = title.strip(" ,;:-")
    if len(title) <= MAX_TITLE_CHARS:
        return title
    return title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0]


def clean_keywords(raw: list[str]) -> tuple[str, ...]:
    """Normalize Claude's keywords: split on commas, drop duplicates, AI wording, numbers, file info."""
    seen: set[str] = set()
    kept: list[str] = []
    for entry in raw:
        for keyword in entry.replace(";", ",").split(","):  # a comma would split it in the CSV anyway
            normalized = " ".join(keyword.split()).lower()
            if not normalized or normalized in seen or AI_TERM_PATTERN.search(normalized):
                continue
            if keyword_problem(normalized):
                continue
            seen.add(normalized)
            kept.append(normalized)
    return tuple(kept[:MAX_KEYWORDS])


def _category_code(raw: object) -> str:
    """The category as a CSV code string; anything outside Adobe's list is dropped (optional field)."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw in CATEGORIES:
        return str(raw)
    return ""


CLAUDE_PATH_ENV = "ADOBE_CLAUDE_PATH"
# Apps started from Finder or the Start menu do not get the shell's PATH, so look where Claude Code
# usually installs itself.
_CLAUDE_DIR_PATTERNS = (
    "~/.local/bin",
    "~/.claude/local",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.nvm/versions/node/*/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/AppData/Roaming/npm",
    "~/AppData/Local/Programs/claude",
)


def find_claude() -> str | None:
    """Full path of the `claude` executable: ADOBE_CLAUDE_PATH, then PATH plus common install dirs."""
    configured = os.environ.get(CLAUDE_PATH_ENV, "").strip()
    if configured:
        return configured if Path(configured).exists() else None
    extra = [
        match for pattern in _CLAUDE_DIR_PATTERNS for match in glob.glob(str(Path(pattern).expanduser()))
    ]
    return shutil.which("claude", path=os.pathsep.join([os.environ.get("PATH", ""), *extra]))


def build_command(preview_dir: Path, names: list[str], model: str) -> list[str]:
    prompt = (
        "Describe each of these images (files in the current directory):\n"
        + "\n".join(names)
        + "\nReturn one item per image; 'file' must be the exact file name listed."
    )
    return [
        find_claude() or "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--tools",
        "Read",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--system-prompt",
        SYSTEM_PROMPT,
        "--add-dir",
        str(preview_dir),
        "--json-schema",
        json.dumps(RESPONSE_SCHEMA),
    ]


def run_claude(command: list[str], cwd: Path) -> str:
    """Run the CLI and return stdout; raise ClaudeCallError if the call as a whole failed."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=CALL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise ClaudeCallError(
            f"Claude Code (`claude`) was not found. Install it and log in, or set {CLAUDE_PATH_ENV}."
        ) from error
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
        content_type = str(item.get("content_type", ""))
        results.append(
            CaptionResult(
                source,
                title,
                keywords,
                _category_code(item.get("category")),
                people_visible=item.get("people_visible") is True,
                content_type=content_type if content_type in CONTENT_TYPES else "",
                review_reason=" ".join(str(item.get("review_reason") or "").split()),
            )
        )
    return results
