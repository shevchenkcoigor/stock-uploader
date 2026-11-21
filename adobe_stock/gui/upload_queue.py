"""What the upload page shows: one row per file of the batch, with its upload state and progress."""

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from adobe_stock import state

FAILED = "failed"
UPLOADING = "uploading"
QUEUED = "queued"
DONE = "done"

# the order of the Status column: what needs attention first
STATE_RANK = {FAILED: 0, UPLOADING: 1, QUEUED: 2, DONE: 3}
DONE_STATUSES = (state.UPLOADED, state.SUBMITTED)


@dataclass(frozen=True)
class UploadRow:
    filename: str
    size: int
    state: str
    progress: float  # 0..1 of this file
    path: str = ""  # where the picture is, for the preview

    @property
    def sent_bytes(self) -> float:
        return self.size * self.progress


@dataclass(frozen=True)
class QueueTotals:
    total: int
    done: int
    failed: int
    sent_bytes: float
    total_bytes: int

    @property
    def fraction(self) -> float:
        return self.sent_bytes / self.total_bytes if self.total_bytes else 0.0


def rows_from_db(files: Iterable[sqlite3.Row], running: bool) -> list[UploadRow]:
    """Uploaded files are done, pending ones with an error failed; while a run is on, the first of the
    rest is the one being sent."""
    rows: list[UploadRow] = []
    sending_taken = not running
    for row in files:
        if row["status"] in DONE_STATUSES:
            rows.append(UploadRow(row["filename"], row["size"], DONE, 1.0, row["path"]))
        elif row["error"]:
            rows.append(UploadRow(row["filename"], row["size"], FAILED, 0.0, row["path"]))
        elif not sending_taken:
            sending_taken = True
            rows.append(UploadRow(row["filename"], row["size"], UPLOADING, 0.0, row["path"]))
        else:
            rows.append(UploadRow(row["filename"], row["size"], QUEUED, 0.0, row["path"]))
    return rows


def totals_of(rows: Sequence[UploadRow]) -> QueueTotals:
    return QueueTotals(
        total=len(rows),
        done=sum(1 for row in rows if row.state == DONE),
        failed=sum(1 for row in rows if row.state == FAILED),
        sent_bytes=sum(row.sent_bytes for row in rows),
        total_bytes=sum(row.size for row in rows),
    )


SORT_KEYS = {
    "name": lambda row: row.filename.lower(),
    "size": lambda row: row.size,
    "progress": lambda row: row.progress,
    "status": lambda row: (STATE_RANK[row.state], row.filename.lower()),
}


def sorted_rows(rows: Sequence[UploadRow], field: str, descending: bool) -> list[UploadRow]:
    return sorted(rows, key=SORT_KEYS[field], reverse=descending)
