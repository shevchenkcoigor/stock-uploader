"""SQLite state: which files are in which batch and where they are in the pipeline.

File lifecycle: pending -> uploaded -> submitted. A file that failed local checks is
'invalid' and is re-evaluated on every scan. 'submitted' is set manually after the portal
submission (Adobe gives us no API).

`filename` is the name used on the flat SFTP root and in the CSV. It equals the real name
unless another live file already uses it; then a short content hash is appended.
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from adobe_stock.metadata_csv import Item, split_into_batches

PENDING = "pending"
INVALID = "invalid"
UPLOADED = "uploaded"
SUBMITTED = "submitted"

HASH_SUFFIX_CHARS = 8
HASH_CHUNK_BYTES = 1024 * 1024

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
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT,
    updated_at TEXT NOT NULL
);
-- The SFTP root is flat, so a name may be used by only one live (non-invalid) file.
CREATE UNIQUE INDEX IF NOT EXISTS files_live_filename
    ON files(filename) WHERE status != 'invalid';
"""


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
    root: Path,
    images: list[Path],
    metadata: dict[Path, Item],
    check_image: Callable[[Path], list[str]],
    batch_size: int | None = None,
) -> ScanResult:
    """Register new files, skip known ones, and group new valid files into batches.

    check_image is called only for files that are new or changed, so a rescan of a big
    tree stays cheap: known files are recognised by size + mtime without hashing.
    """
    added = unchanged = invalid = 0
    changed_after_upload: list[str] = []
    new_valid: list[tuple[Path, Item]] = []

    with conn:
        for image in images:
            stat = image.stat()
            row = conn.execute("SELECT * FROM files WHERE path = ?", (str(image),)).fetchone()
            known = row is not None and row["status"] != INVALID
            if known and row["size"] == stat.st_size and row["mtime_ns"] == stat.st_mtime_ns:
                unchanged += 1
                continue
            digest = sha256_of(image)
            if known and row["sha256"] == digest:
                conn.execute("UPDATE files SET mtime_ns = ? WHERE id = ?", (stat.st_mtime_ns, row["id"]))
                unchanged += 1
                continue
            if known and row["status"] in (UPLOADED, SUBMITTED):
                changed_after_upload.append(image.name)
                continue

            problems = _problems(conn, image, digest, metadata, check_image)
            if problems:
                _upsert(conn, image, image.name, digest, stat, None, INVALID, "; ".join(problems))
                invalid += 1
                continue
            remote_name = _remote_name(conn, image, digest)
            item = replace(metadata[image], filename=remote_name)
            _upsert(conn, image, remote_name, digest, stat, item, PENDING, None)
            new_valid.append((image, item))
            added += 1
        batches_created = _assign_batches(conn, root, new_valid, batch_size)
    return ScanResult(added, unchanged, invalid, tuple(changed_after_upload), batches_created)


def _problems(conn, image: Path, digest: str, metadata, check_image) -> list[str]:
    problems = list(check_image(image))
    if image not in metadata:
        problems.append("no metadata row")
    duplicate = conn.execute(
        "SELECT path FROM files WHERE sha256 = ? AND path != ? AND status != ?",
        (digest, str(image), INVALID),
    ).fetchone()
    if duplicate:
        problems.append(f"same content as {duplicate['path']}")
    return problems


def _remote_name(conn, image: Path, digest: str) -> str:
    taken = conn.execute(
        "SELECT 1 FROM files WHERE filename = ? AND path != ? AND status != ?",
        (image.name, str(image), INVALID),
    ).fetchone()
    if not taken:
        return image.name
    return f"{image.stem}_{digest[:HASH_SUFFIX_CHARS]}{image.suffix}"


def _upsert(conn, image: Path, filename: str, digest: str, stat, item: Item | None,
            status: str, error: str | None) -> None:
    conn.execute(
        """
        INSERT INTO files (path, filename, sha256, size, mtime_ns, title, keywords, category,
                           status, error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            filename=excluded.filename, sha256=excluded.sha256, size=excluded.size,
            mtime_ns=excluded.mtime_ns, title=excluded.title, keywords=excluded.keywords,
            category=excluded.category, status=excluded.status, error=excluded.error,
            batch_id=NULL, updated_at=excluded.updated_at
        """,
        (
            str(image), filename, digest, stat.st_size, stat.st_mtime_ns,
            item.title if item else "", json.dumps(list(item.keywords)) if item else "[]",
            item.category if item else "", status, error, _now(),
        ),
    )


def _assign_batches(conn, root: Path, new_valid: list[tuple[Path, Item]],
                    batch_size: int | None) -> int:
    if not new_valid:
        return 0
    path_by_name = {item.filename: image for image, item in new_valid}
    next_part = conn.execute(
        "SELECT COALESCE(MAX(part), 0) + 1 FROM batches WHERE folder = ?", (str(root),)
    ).fetchone()[0]
    groups = split_into_batches([item for _, item in new_valid], batch_size)
    for offset, group in enumerate(groups):
        cursor = conn.execute(
            "INSERT INTO batches (folder, part, created_at) VALUES (?, ?, ?)",
            (str(root), next_part + offset, _now()),
        )
        conn.executemany(
            "UPDATE files SET batch_id = ? WHERE path = ?",
            [(cursor.lastrowid, str(path_by_name[item.filename])) for item in group],
        )
    return len(groups)


def invalid_files(conn: sqlite3.Connection, root: Path) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT path, error FROM files WHERE status = ? AND path LIKE ? ORDER BY path",
        (INVALID, f"{root}/%"),
    ).fetchall()


def pending_files(conn: sqlite3.Connection, batch_id: int | None = None) -> list[tuple[Path, str]]:
    """Pending files as (local path, remote name)."""
    query = "SELECT path, filename FROM files WHERE status = ?"
    params: list = [PENDING]
    if batch_id is not None:
        query += " AND batch_id = ?"
        params.append(batch_id)
    return [(Path(r["path"]), r["filename"]) for r in conn.execute(query + " ORDER BY id", params)]


def pending_batches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Batches that still have pending files, oldest first, with their pending counts."""
    return conn.execute(
        """
        SELECT b.id AS batch_id, b.folder, b.part, COUNT(*) AS n
        FROM files f JOIN batches b ON b.id = f.batch_id
        WHERE f.status = ?
        GROUP BY b.id ORDER BY b.id
        """,
        (PENDING,),
    ).fetchall()


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


def recorded_csv_path(conn: sqlite3.Connection, batch_id: int) -> Path | None:
    row = conn.execute("SELECT csv_path FROM batches WHERE id = ?", (batch_id,)).fetchone()
    return Path(row["csv_path"]) if row and row["csv_path"] else None


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
