"""SQLite state: which files are in which batch and where they are in the pipeline.

File lifecycle: pending -> uploaded -> submitted. A file that failed local checks is
'invalid'. 'submitted' is set manually after the portal submission (Adobe gives us no API).
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from adobe_stock.metadata_csv import Item, split_into_batches

PENDING = "pending"
INVALID = "invalid"
UPLOADED = "uploaded"
SUBMITTED = "submitted"

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY,
    folder TEXT NOT NULL,
    part INTEGER NOT NULL,
    csv_path TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (folder, part)
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER REFERENCES batches(id),
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT,
    updated_at TEXT NOT NULL
);
-- The SFTP root is flat, so a filename may be used by only one live (non-invalid) file.
CREATE UNIQUE INDEX IF NOT EXISTS files_live_filename
    ON files(filename) WHERE status != 'invalid';
"""
HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ScanResult:
    added: int
    unchanged: int
    invalid: int
    changed_after_upload: tuple[str, ...]
    batches_created: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def scan_folder(
    conn: sqlite3.Connection,
    folder: Path,
    images: list[Path],
    metadata: dict[str, Item],
    problems_by_image: dict[Path, list[str]],
) -> ScanResult:
    """Register new files, ignore known ones, and group new valid files into batches."""
    added = unchanged = invalid = 0
    changed_after_upload: list[str] = []
    new_valid: list[tuple[Path, Item]] = []

    with conn:
        for image in images:
            digest = sha256_of(image)
            row = conn.execute("SELECT * FROM files WHERE path = ?", (str(image),)).fetchone()
            if row and row["sha256"] == digest:
                unchanged += 1
                continue
            if row and row["status"] in (UPLOADED, SUBMITTED):
                changed_after_upload.append(image.name)
                continue
            problems = _problems(conn, image, metadata, problems_by_image)
            if problems:
                _upsert(conn, image, digest, None, INVALID, "; ".join(problems))
                invalid += 1
                continue
            _upsert(conn, image, digest, metadata[image.name], PENDING, None)
            new_valid.append((image, metadata[image.name]))
            added += 1
        batches_created = _assign_batches(conn, folder, new_valid)
    return ScanResult(added, unchanged, invalid, tuple(changed_after_upload), batches_created)


def _problems(conn, image: Path, metadata, problems_by_image) -> list[str]:
    problems = list(problems_by_image.get(image, []))
    if image.name not in metadata:
        problems.append("no metadata row")
    other = conn.execute(
        "SELECT path FROM files WHERE filename = ? AND path != ? AND status != ?",
        (image.name, str(image), INVALID),
    ).fetchone()
    if other:
        problems.append(f"filename already used by {other['path']} (SFTP root is flat)")
    return problems


def _upsert(conn, image: Path, digest: str, item: Item | None, status: str, error: str | None):
    conn.execute(
        """
        INSERT INTO files (path, filename, sha256, size, title, keywords, category, status, error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            sha256=excluded.sha256, size=excluded.size, title=excluded.title,
            keywords=excluded.keywords, category=excluded.category, status=excluded.status,
            error=excluded.error, batch_id=NULL, updated_at=excluded.updated_at
        """,
        (
            str(image), image.name, digest, image.stat().st_size,
            item.title if item else "", json.dumps(list(item.keywords)) if item else "[]",
            item.category if item else "", status, error, _now(),
        ),
    )


def _assign_batches(conn, folder: Path, new_valid: list[tuple[Path, Item]]) -> int:
    if not new_valid:
        return 0
    path_by_name = {item.filename: image for image, item in new_valid}
    next_part = conn.execute(
        "SELECT COALESCE(MAX(part), 0) + 1 FROM batches WHERE folder = ?", (str(folder),)
    ).fetchone()[0]
    groups = split_into_batches([item for _, item in new_valid])
    for offset, group in enumerate(groups):
        cursor = conn.execute(
            "INSERT INTO batches (folder, part, created_at) VALUES (?, ?, ?)",
            (str(folder), next_part + offset, _now()),
        )
        conn.executemany(
            "UPDATE files SET batch_id = ? WHERE path = ?",
            [(cursor.lastrowid, str(path_by_name[item.filename])) for item in group],
        )
    return len(groups)


def pending_files(conn: sqlite3.Connection, batch_id: int | None = None) -> list[Path]:
    query = "SELECT path FROM files WHERE status = ?"
    params: list = [PENDING]
    if batch_id is not None:
        query += " AND batch_id = ?"
        params.append(batch_id)
    return [Path(row["path"]) for row in conn.execute(query + " ORDER BY id", params)]


def mark_uploaded(conn: sqlite3.Connection, path: Path) -> None:
    with conn:
        conn.execute(
            "UPDATE files SET status = ?, error = NULL, updated_at = ? WHERE path = ?",
            (UPLOADED, _now(), str(path)),
        )


def batch_items(conn: sqlite3.Connection, batch_id: int) -> list[Item]:
    """Uploaded, not yet submitted files of a batch: exactly what the portal CSV may cover."""
    rows = conn.execute(
        "SELECT * FROM files WHERE batch_id = ? AND status = ? ORDER BY id", (batch_id, UPLOADED)
    )
    return [
        Item(r["filename"], r["title"], tuple(json.loads(r["keywords"])), r["category"])
        for r in rows
    ]


def record_csv(conn: sqlite3.Connection, batch_id: int, csv_path: Path) -> None:
    with conn:
        conn.execute("UPDATE batches SET csv_path = ? WHERE id = ?", (str(csv_path), batch_id))


def mark_batch_submitted(conn: sqlite3.Connection, batch_id: int) -> int:
    with conn:
        cursor = conn.execute(
            "UPDATE files SET status = ?, updated_at = ? WHERE batch_id = ? AND status = ?",
            (SUBMITTED, _now(), batch_id, UPLOADED),
        )
    return cursor.rowcount


def status_summary(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT b.id AS batch_id, b.folder, b.part, b.csv_path, f.status, COUNT(*) AS n
        FROM files f LEFT JOIN batches b ON b.id = f.batch_id
        GROUP BY b.id, f.status ORDER BY b.id, f.status
        """
    ).fetchall()
