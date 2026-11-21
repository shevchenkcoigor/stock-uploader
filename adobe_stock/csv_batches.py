"""What the CSV page lists: the batches that have a CSV, their rows and the state of each CSV."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from adobe_stock import state
from adobe_stock.metadata_csv import Item, check_items

BUILT = "built"
SENT = "sent"
ERROR = "error"
CSV_FILE_STATUSES = (state.UPLOADED, state.SUBMITTED)


@dataclass(frozen=True)
class CsvEntry:
    batch_id: int
    name: str
    rows: int
    bad_rows: int
    state: str


def item_of(row: sqlite3.Row) -> Item:
    return Item(row["filename"], row["title"], tuple(json.loads(row["keywords"])), row["category"])


def row_has_problem(row: sqlite3.Row) -> bool:
    """True when Adobe would not take this row as it is: no title, no keywords or a wrong category."""
    item = item_of(row)
    return bool(check_items([item], {item.filename}))


def csv_rows(conn: sqlite3.Connection, batch_id: int) -> list[sqlite3.Row]:
    """The files a batch's CSV covers: uploaded ones, and submitted ones once the batch is sent."""
    marks = ",".join("?" * len(CSV_FILE_STATUSES))
    return conn.execute(
        "SELECT filename, title, keywords, category, status FROM files "
        f"WHERE batch_id = ? AND status IN ({marks}) ORDER BY id",
        (batch_id, *CSV_FILE_STATUSES),
    ).fetchall()


def csv_entries(conn: sqlite3.Connection) -> list[CsvEntry]:
    """Batches with a built CSV, newest first."""
    batches = conn.execute(
        "SELECT id, csv_path FROM batches WHERE csv_path IS NOT NULL ORDER BY id DESC"
    ).fetchall()
    entries = []
    for batch in batches:
        rows = csv_rows(conn, batch["id"])
        if rows:
            entries.append(_entry(batch["id"], Path(batch["csv_path"]), rows))
    return entries


def _entry(batch_id: int, path: Path, rows: list[sqlite3.Row]) -> CsvEntry:
    bad = sum(1 for row in rows if row_has_problem(row))
    waiting = any(row["status"] == state.UPLOADED for row in rows)
    if not waiting:
        kind = SENT
    elif bad or not path.exists():
        kind = ERROR
    else:
        kind = BUILT
    return CsvEntry(batch_id, path.name, len(rows), bad, kind)


def batches_without_csv(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Batches that have uploaded files but no CSV yet: what «+ CSV» offers."""
    return conn.execute(
        "SELECT b.id AS batch_id, b.folder, b.part, COUNT(*) AS n FROM batches b "
        "JOIN files f ON f.batch_id = b.id AND f.status = ? "
        "WHERE b.csv_path IS NULL GROUP BY b.id ORDER BY b.id",
        (state.UPLOADED,),
    ).fetchall()
