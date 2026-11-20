"""Build and validate the Adobe Stock metadata CSV.

HEADERS must match Adobe's official CSV template exactly. They are taken from
the project notes and are UNVERIFIED: compare with the template downloaded from
the Contributor Portal and edit this tuple if they differ.
"""

import csv
import io
from dataclasses import dataclass
from pathlib import Path

HEADERS = ("Filename", "Title", "Keywords", "Category", "Created using generative AI tools")
MAX_ROWS = 5000
MAX_CSV_BYTES = 1024 * 1024
MAX_KEYWORDS = 49
MAX_TITLE_CHARS = 200
AI_FLAG_VALUE = "Yes"


@dataclass(frozen=True)
class Item:
    filename: str
    title: str
    keywords: tuple[str, ...]
    category: str = ""
    ai_generated: bool = True


def read_source(path: Path) -> list[Item]:
    """Read our own input CSV: filename,title,keywords[,category][,ai_generated].

    Rule: every item is AI-generated. A missing ai_generated cell means True;
    an explicit "no" is rejected later by check_items.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return [
        Item(
            filename=row["filename"].strip(),
            title=row["title"].strip(),
            keywords=tuple(k.strip() for k in row["keywords"].split(",") if k.strip()),
            category=(row.get("category") or "").strip(),
            ai_generated=_parse_flag(row.get("ai_generated"), True),
        )
        for row in rows
    ]


def _parse_flag(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def check_items(items: list[Item], existing_files: set[str]) -> list[str]:
    """Return problems found in the metadata, including filename mismatches."""
    problems: list[str] = []
    if len(items) > MAX_ROWS:
        problems.append(f"{len(items)} rows, limit is {MAX_ROWS}")
    seen: set[str] = set()
    for item in items:
        if item.filename in seen:
            problems.append(f"{item.filename}: duplicate row")
        seen.add(item.filename)
        if item.filename not in existing_files:
            problems.append(f"{item.filename}: no such file among uploads")
        if not item.ai_generated:
            problems.append(f"{item.filename}: must be marked as AI-generated")
        if not item.title:
            problems.append(f"{item.filename}: empty title")
        if len(item.title) > MAX_TITLE_CHARS:
            problems.append(f"{item.filename}: title longer than {MAX_TITLE_CHARS}")
        if not item.keywords:
            problems.append(f"{item.filename}: no keywords")
        if len(item.keywords) > MAX_KEYWORDS:
            problems.append(f"{item.filename}: more than {MAX_KEYWORDS} keywords")
    return problems


def write_adobe_csv(items: list[Item], destination: Path) -> None:
    """Write UTF-8 CSV with Adobe headers and verify it stays within 1 MB.

    The AI flag is always written; refuse to write items not marked as AI.
    """
    unmarked = [item.filename for item in items if not item.ai_generated]
    if unmarked:
        raise ValueError(f"items not marked as AI-generated: {', '.join(unmarked)}")
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


def _row(item: Item) -> tuple[str, ...]:
    return (item.filename, item.title, ", ".join(item.keywords), item.category, AI_FLAG_VALUE)


def estimate_row_bytes(item: Item) -> int:
    """UTF-8 size of one CSV row, used to split batches under the 1 MB limit."""
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerow(_row(item))
    return len(buffer.getvalue().encode("utf-8"))


def split_into_batches(items: list[Item]) -> list[list[Item]]:
    """Group items so that each group fits both the row limit and the 1 MB limit."""
    header_bytes = len(",".join(HEADERS).encode("utf-8")) + 2
    batches: list[list[Item]] = []
    current: list[Item] = []
    used = header_bytes
    for item in items:
        row_bytes = estimate_row_bytes(item)
        if current and (len(current) >= MAX_ROWS or used + row_bytes > MAX_CSV_BYTES):
            batches.append(current)
            current, used = [], header_bytes
        current.append(item)
        used += row_bytes
    if current:
        batches.append(current)
    return batches
