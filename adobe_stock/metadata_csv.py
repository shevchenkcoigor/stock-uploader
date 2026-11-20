"""Build and validate the Adobe Stock metadata CSV.

Format (from Adobe's "Upload CSV files" help page, checked 2026-11-20):
- column names must match Adobe's sample CSV exactly, in English; unknown columns are ignored
- only Filename is required; Title (max 200 chars), Keywords (max 49, comma-separated,
  ordered by relevance), Category (numeric code) and Releases are optional
- at most 20,000 rows and 20 MB per CSV, UTF-8
There is no generative-AI column: that flag is set in the portal, not in the CSV.
"""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from adobe_stock.categories import CATEGORIES

HEADERS = ("Filename", "Title", "Keywords", "Category", "Releases")
MAX_ROWS = 20_000
MAX_CSV_BYTES = 20 * 1024 * 1024
MAX_KEYWORDS = 49
MAX_TITLE_CHARS = 200
# Adobe labels generative-AI content itself (portal checkbox), so titles and keywords must not
# announce it or name AI tools.
AI_TERM_PATTERN = re.compile(
    r"\b(?:generative(?:\s+ai)?|ai[\s-]+generated|artificial\s+intelligence|generated|ai|"
    r"midjourney|dall[\s-]?e|stable\s+diffusion|chatgpt|firefly)\b",
    re.IGNORECASE,
)


# Adobe: no numbers or file information (camera, size, format) in keywords.
_NUMBER_ONLY = re.compile(r"^[\d\s.,x*-]+$", re.IGNORECASE)
_FILE_INFO = re.compile(r"\b(?:jpe?g|png|tiff?|\d+\s*(?:mp|mb|kb|px|dpi)|megapixels?)\b", re.IGNORECASE)


def keyword_problem(keyword: str) -> str | None:
    """Why Adobe would not accept this single keyword, or None if it is fine."""
    if "," in keyword or ";" in keyword:
        return "contains a comma or semicolon (it would be split into several keywords)"
    if _NUMBER_ONLY.match(keyword):
        return "is only a number"
    if _FILE_INFO.search(keyword):
        return "looks like file information"
    return None


@dataclass(frozen=True)
class Item:
    filename: str
    title: str
    keywords: tuple[str, ...]
    category: str = ""


def read_source(path: Path) -> list[Item]:
    """Read our own input CSV: filename,title,keywords[,category]. Extra columns are ignored."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return [
        Item(
            filename=row["filename"].strip(),
            title=row["title"].strip(),
            keywords=tuple(k.strip() for k in row["keywords"].split(",") if k.strip()),
            category=(row.get("category") or "").strip(),
        )
        for row in rows
    ]


def check_items(items: list[Item], existing_files: set[str]) -> list[str]:
    """Return problems found in the metadata, including filename mismatches."""
    problems: list[str] = []
    if len(items) > MAX_ROWS:
        problems.append(f"{len(items)} rows, limit is {MAX_ROWS}")
    seen: set[str] = set()
    for item in items:
        if not item.filename:
            problems.append("a row has an empty filename")
        if item.filename in seen:
            problems.append(f"{item.filename}: duplicate row")
        seen.add(item.filename)
        if item.filename not in existing_files:
            problems.append(f"{item.filename}: no such file among uploads")
        if not item.title:
            problems.append(f"{item.filename}: empty title")
        if len(item.title) > MAX_TITLE_CHARS:
            problems.append(f"{item.filename}: title longer than {MAX_TITLE_CHARS}")
        found = AI_TERM_PATTERN.search(item.title)
        if found:
            problems.append(
                f"{item.filename}: title mentions {found.group()!r}; Adobe labels AI content itself"
            )
        if not item.keywords:
            problems.append(f"{item.filename}: no keywords")
        if len(item.keywords) > MAX_KEYWORDS:
            problems.append(f"{item.filename}: more than {MAX_KEYWORDS} keywords")
        banned = next((k for k in item.keywords if AI_TERM_PATTERN.search(k)), None)
        if banned:
            problems.append(
                f"{item.filename}: keyword {banned!r} mentions AI; Adobe labels AI content itself"
            )
        for keyword in item.keywords:
            reason = keyword_problem(keyword)
            if reason:
                problems.append(f"{item.filename}: keyword {keyword!r} {reason}")
        if item.category and not (item.category.isdigit() and int(item.category) in CATEGORIES):
            problems.append(
                f"{item.filename}: category must be a code from 1-{len(CATEGORIES)}, got {item.category!r}"
            )
    return problems


def with_ai_label(title: str, label: str) -> str:
    """End the title with `label` (e.g. "Generative AI"), keeping it within the title limit.

    An empty label leaves the title alone; a title that already ends with the label is kept.
    """
    label = label.strip()
    if not label or title.lower().endswith(label.lower()):
        return title
    room = MAX_TITLE_CHARS - len(label) - 1
    body = title if len(title) <= room else title[:room].rsplit(" ", 1)[0] or title[:room]
    return f"{body} {label}"


def write_adobe_csv(items: list[Item], destination: Path) -> None:
    """Write a UTF-8 CSV with Adobe's headers; refuse to write one over the Adobe limits."""
    if len(items) > MAX_ROWS:
        raise ValueError(f"{len(items)} rows, Adobe limit is {MAX_ROWS}")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for item in items:
        writer.writerow(_row(item))
    content = buffer.getvalue().encode("utf-8")
    if len(content) > MAX_CSV_BYTES:
        raise ValueError(f"CSV is {len(content)} bytes, Adobe limit is {MAX_CSV_BYTES}")
    destination.write_bytes(content)


def validate_csv_file(path: Path, expected_filenames: set[str]) -> list[str]:
    """Re-read a written CSV and check it against Adobe's rules, as the portal would."""
    if path.stat().st_size > MAX_CSV_BYTES:
        return [f"file is larger than {MAX_CSV_BYTES} bytes"]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["file is not valid UTF-8"]
    rows = list(csv.reader(io.StringIO(text, newline="")))
    problems: list[str] = []
    if not rows or tuple(rows[0]) != HEADERS:
        problems.append(f"headers {rows[0] if rows else []} differ from {list(HEADERS)}")
    data = rows[1:]
    if len(data) > MAX_ROWS:
        problems.append(f"{len(data)} rows, limit is {MAX_ROWS}")
    if any(len(row) != len(HEADERS) for row in data):
        problems.append("some rows do not have exactly 5 columns")
    if any(len(row) > 1 and len(row[1]) > MAX_TITLE_CHARS for row in data):
        problems.append(f"some titles are longer than {MAX_TITLE_CHARS} characters")
    names = [row[0] for row in data if row]
    if len(set(names)) != len(names):
        problems.append("duplicate filenames")
    if set(names) != expected_filenames:
        problems.append("filenames differ from the files that were uploaded")
    return problems


def _row(item: Item) -> tuple[str, ...]:
    # Releases is left empty: AI images normally have no model or property releases.
    return (item.filename, item.title, ", ".join(item.keywords), item.category, "")


def estimate_row_bytes(item: Item) -> int:
    """UTF-8 size of one CSV row, used to keep batches under the size limit."""
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerow(_row(item))
    return len(buffer.getvalue().encode("utf-8"))


def split_into_batches(items: list[Item], batch_size: int | None = None) -> list[list[Item]]:
    """Group items so that each group fits batch_size, the row limit and the size limit."""
    max_rows = min(batch_size or MAX_ROWS, MAX_ROWS)
    header_bytes = len(",".join(HEADERS).encode("utf-8")) + 2
    batches: list[list[Item]] = []
    current: list[Item] = []
    used = header_bytes
    for item in items:
        row_bytes = estimate_row_bytes(item)
        if current and (len(current) >= max_rows or used + row_bytes > MAX_CSV_BYTES):
            batches.append(current)
            current, used = [], header_bytes
        current.append(item)
        used += row_bytes
    if current:
        batches.append(current)
    return batches
