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
from datetime import UTC, datetime
from pathlib import Path

from adobe_stock.metadata_csv import MAX_KEYWORDS, Item, split_into_batches

PENDING = "pending"
INVALID = "invalid"
UPLOADED = "uploaded"
SUBMITTED = "submitted"
NEEDS_METADATA = "needs_metadata"  # valid image, waiting for a title and keywords
REVIEW = "review"  # captioned, but flagged as a possible Adobe AI-policy risk; needs a human

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
    root TEXT NOT NULL DEFAULT '',
    people_visible INTEGER NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT,
    updated_at TEXT NOT NULL
);
-- Append-only history of what the commands did; never rewritten by other operations.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    command TEXT NOT NULL,
    level TEXT NOT NULL,
    filename TEXT,
    message TEXT NOT NULL
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
    needs_metadata: int
    changed_after_upload: tuple[str, ...]
    batches_created: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    return conn


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Upgrade databases created before a column existed."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(files)")}
    additions = {
        "mtime_ns": "INTEGER NOT NULL DEFAULT 0",
        "root": "TEXT NOT NULL DEFAULT ''",
        "people_visible": "INTEGER NOT NULL DEFAULT 0",
        "content_type": "TEXT NOT NULL DEFAULT ''",
    }
    with conn:
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE files ADD COLUMN {name} {ddl}")


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
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ScanResult:
    """Register new files, skip known ones, and group new valid files into batches.

    on_progress(done, total, path) is called before each file; should_stop() ends the scan early
    (what was registered so far is kept).

    check_image is called only for files that are new or changed, so a rescan of a big
    tree stays cheap: known files are recognised by size + mtime without hashing.
    """
    added = unchanged = invalid = needs_metadata = 0
    changed_after_upload: list[str] = []

    with conn:
        for position, image in enumerate(images):
            if should_stop is not None and should_stop():
                break
            if on_progress is not None:
                on_progress(position, len(images), str(image))
            stat = image.stat()
            row = conn.execute("SELECT * FROM files WHERE path = ?", (str(image),)).fetchone()
            known = row if row is not None and row["status"] != INVALID else None
            if known is not None and known["status"] == NEEDS_METADATA and image in metadata:
                known = None  # a metadata row appeared since the last scan
            if known is not None and known["size"] == stat.st_size and known["mtime_ns"] == stat.st_mtime_ns:
                unchanged += 1
                continue
            digest = sha256_of(image)
            if known is not None and known["sha256"] == digest:
                conn.execute("UPDATE files SET mtime_ns = ? WHERE id = ?", (stat.st_mtime_ns, known["id"]))
                unchanged += 1
                continue
            if known is not None and known["status"] in (UPLOADED, SUBMITTED):
                changed_after_upload.append(image.name)
                continue

            problems = _problems(conn, image, digest, check_image)
            if problems:
                _upsert(conn, image, image.name, digest, stat, None, INVALID, "; ".join(problems), root)
                invalid += 1
                continue
            remote_name = _remote_name(conn, image, digest)
            if image in metadata:
                item = replace(metadata[image], filename=remote_name)
                _upsert(conn, image, remote_name, digest, stat, item, PENDING, None, root)
                added += 1
            else:
                _upsert(conn, image, remote_name, digest, stat, None, NEEDS_METADATA, None, root)
                needs_metadata += 1
        batches_created = batch_unassigned(conn, batch_size)
    return ScanResult(added, unchanged, invalid, needs_metadata, tuple(changed_after_upload), batches_created)


def _problems(conn, image: Path, digest: str, check_image) -> list[str]:
    problems = list(check_image(image))
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


def _upsert(
    conn,
    image: Path,
    filename: str,
    digest: str,
    stat,
    item: Item | None,
    status: str,
    error: str | None,
    root: Path,
) -> None:
    conn.execute(
        """
        INSERT INTO files (path, filename, sha256, size, mtime_ns, root, title, keywords, category,
                           status, error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            filename=excluded.filename, sha256=excluded.sha256, size=excluded.size,
            mtime_ns=excluded.mtime_ns, root=excluded.root, title=excluded.title,
            keywords=excluded.keywords,
            category=excluded.category, status=excluded.status, error=excluded.error,
            batch_id=NULL, updated_at=excluded.updated_at
        """,
        (
            str(image),
            filename,
            digest,
            stat.st_size,
            stat.st_mtime_ns,
            str(root),
            item.title if item else "",
            json.dumps(list(item.keywords)) if item else "[]",
            item.category if item else "",
            status,
            error,
            _now(),
        ),
    )


def _item_from_row(row: sqlite3.Row) -> Item:
    return Item(row["filename"], row["title"], tuple(json.loads(row["keywords"])), row["category"])


def batch_unassigned(conn: sqlite3.Connection, batch_size: int | None = None) -> int:
    """Put pending files that have no batch yet into new batches, grouped by scan root.

    The caller owns the transaction. Returns the number of batches created.
    """
    rows = conn.execute(
        "SELECT * FROM files WHERE status = ? AND batch_id IS NULL ORDER BY id", (PENDING,)
    ).fetchall()
    by_root: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_root.setdefault(row["root"], []).append(row)
    created = 0
    for root, group in by_root.items():
        entries = [(Path(row["path"]), _item_from_row(row)) for row in group]
        created += _assign_batches(conn, Path(root), entries, batch_size)
    return created


def assign_batches(conn: sqlite3.Connection, batch_size: int | None = None) -> int:
    with conn:
        return batch_unassigned(conn, batch_size)


def files_needing_metadata(
    conn: sqlite3.Connection, limit: int, names: list[str] | None = None
) -> list[Path]:
    """New files waiting for a title and keywords; `names` narrows it to those files (None: all of them)."""
    if names is None:
        rows = conn.execute(
            "SELECT path FROM files WHERE status = ? ORDER BY id LIMIT ?", (NEEDS_METADATA, limit)
        )
    else:
        rows = conn.execute(
            "SELECT path FROM files WHERE status = ? AND filename IN (SELECT value FROM json_each(?)) "
            "ORDER BY id LIMIT ?",
            (NEEDS_METADATA, json.dumps(names), limit),
        )
    return [Path(row["path"]) for row in rows]


def set_metadata(
    conn: sqlite3.Connection,
    path: Path,
    title: str,
    keywords: tuple[str, ...],
    category: str = "",
    people_visible: bool = False,
    content_type: str = "",
    review_reason: str = "",
) -> None:
    """Attach generated metadata.

    The file becomes pending (ready to be batched), unless review_reason is set: then it goes to
    `review` and stays out of batches until a person approves it.
    """
    status = REVIEW if review_reason else PENDING
    with conn:
        conn.execute(
            "UPDATE files SET title = ?, keywords = ?, category = ?, people_visible = ?, "
            "content_type = ?, status = ?, error = ?, updated_at = ? WHERE path = ? AND status = ?",
            (
                title,
                json.dumps(list(keywords)),
                category,
                int(people_visible),
                content_type,
                status,
                review_reason or None,
                _now(),
                str(path),
                NEEDS_METADATA,
            ),
        )


def all_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every file with what the description page shows (newest last)."""
    return conn.execute(
        "SELECT filename, path, size, title, keywords, category, status, error, updated_at "
        "FROM files ORDER BY id"
    ).fetchall()


# What the Description page lists and lets a person edit: new, described, flagged for review, uploaded.
# Invalid files stay on the scan page; submitted ones are final.
EDITABLE_STATUSES = (NEEDS_METADATA, PENDING, REVIEW, UPLOADED)


def files_to_describe(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Files shown on the Description page: new, described, flagged for review and uploaded ones."""
    marks = ",".join("?" * len(EDITABLE_STATUSES))
    return conn.execute(
        "SELECT filename, path, size, title, keywords, category, status, error, updated_at "
        f"FROM files WHERE status IN ({marks}) ORDER BY id",
        EDITABLE_STATUSES,
    ).fetchall()


def update_metadata(
    conn: sqlite3.Connection, filename: str, title: str, keywords: tuple[str, ...], category: str
) -> bool:
    """Save a person's edit. A file waiting for metadata becomes pending once it has a title and keywords.

    Only the statuses in EDITABLE_STATUSES: not invalid ones and not those already submitted.
    """
    marks = ",".join("?" * len(EDITABLE_STATUSES))
    with conn:
        changed = conn.execute(
            "UPDATE files SET title = ?, keywords = ?, category = ?, updated_at = ?, "
            "status = CASE WHEN status = ? AND ? != '' AND ? != '[]' THEN ? ELSE status END "
            f"WHERE filename = ? AND status IN ({marks})",
            (
                title,
                json.dumps(list(keywords)),
                category,
                _now(),
                NEEDS_METADATA,
                title,
                json.dumps(list(keywords)),
                PENDING,
                filename,
                *EDITABLE_STATUSES,
            ),
        ).rowcount
    return changed > 0


def bulk_edit(
    conn: sqlite3.Connection, filenames: list[str], tag: str = "", category: str | None = None
) -> int:
    """Add one keyword to, and/or set the category of, several files at once; returns how many changed.

    The keyword is appended (never twice, at most MAX_KEYWORDS), the category replaces the old one.
    Only files that may still be described are touched; a described one stays described.
    """
    tag = tag.strip()
    if not filenames or (not tag and category is None):
        return 0
    marks = ",".join("?" * len(EDITABLE_STATUSES))
    changed = 0
    with conn:
        for filename in filenames:
            row = conn.execute(
                f"SELECT title, keywords, category FROM files WHERE filename = ? AND status IN ({marks})",
                (filename, *EDITABLE_STATUSES),
            ).fetchone()
            if row is None:
                continue
            keywords = list(json.loads(row["keywords"]))
            if tag and tag not in keywords and len(keywords) < MAX_KEYWORDS:
                keywords.append(tag)
            update_metadata(
                conn,
                filename,
                row["title"],
                tuple(keywords),
                row["category"] if category is None else category,
            )
            changed += 1
    return changed


MANUAL_REVIEW_REASON = "Отмечено на проверку вручную"


def set_status(conn: sqlite3.Connection, filenames: list[str], status: str) -> int:
    """Move files between the statuses of the Description page by hand; returns how many were moved.

    Only files of those statuses move (submitted and invalid ones never do), and a file already in
    `status` is not counted. Everything but "new" needs a title and keywords: a file without them stays
    as it is. The text of a file moved back to "new" is kept, so it can be captioned again.
    """
    if status not in EDITABLE_STATUSES:
        raise ValueError(f"not a status of the description page: {status}")
    if not filenames:
        return 0
    marks = ",".join("?" * len(EDITABLE_STATUSES))
    needs_text = "" if status == NEEDS_METADATA else " AND title != '' AND keywords != '[]'"
    reason = MANUAL_REVIEW_REASON if status == REVIEW else None
    with conn:
        return conn.execute(
            f"UPDATE files SET status = ?, error = ?, updated_at = ? WHERE status != ? "
            f"AND status IN ({marks}) AND filename IN (SELECT value FROM json_each(?)){needs_text}",
            (status, reason, _now(), status, *EDITABLE_STATUSES, json.dumps(filenames)),
        ).rowcount


def review_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT filename, path, title, error FROM files WHERE status = ? ORDER BY id", (REVIEW,)
    ).fetchall()


def approve_reviewed(conn: sqlite3.Connection, filenames: list[str] | None = None) -> int:
    """Release flagged files into the pending queue; None approves all of them."""
    query = "UPDATE files SET status = ?, error = NULL, updated_at = ? WHERE status = ?"
    params: list = [PENDING, _now(), REVIEW]
    if filenames is not None:
        query += f" AND filename IN ({','.join('?' * len(filenames))})"
        params.extend(filenames)
    with conn:
        return conn.execute(query, params).rowcount


def portal_notes(conn: sqlite3.Connection, batch_id: int) -> list[sqlite3.Row]:
    """Per-file facts needed for the portal checkboxes, for the uploaded files of a batch."""
    return conn.execute(
        "SELECT filename, people_visible, content_type FROM files "
        "WHERE batch_id = ? AND status = ? ORDER BY id",
        (batch_id, UPLOADED),
    ).fetchall()


def record_error(conn: sqlite3.Connection, path: Path, error: str) -> None:
    with conn:
        conn.execute("UPDATE files SET error = ?, updated_at = ? WHERE path = ?", (error, _now(), str(path)))


def _assign_batches(conn, root: Path, new_valid: list[tuple[Path, Item]], batch_size: int | None) -> int:
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
    """Invalid files under `root`. Compared as paths in Python: a LIKE pattern breaks on
    backslashes (Windows) and treats `_` and `%` in folder names as wildcards."""
    rows = conn.execute("SELECT path, error FROM files WHERE status = ? ORDER BY path", (INVALID,))
    return [row for row in rows if Path(row["path"]).is_relative_to(root)]


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
    return [Item(r["filename"], r["title"], tuple(json.loads(r["keywords"])), r["category"]) for r in rows]


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


def log_event(
    conn: sqlite3.Connection, command: str, message: str, level: str = "info", filename: str | None = None
) -> None:
    """Append one line to the history (level: info, warning or error)."""
    with conn:
        conn.execute(
            "INSERT INTO events (at, command, level, filename, message) VALUES (?, ?, ?, ?, ?)",
            (_now(), command, level, filename, message),
        )


def recent_events(
    conn: sqlite3.Connection, limit: int, command: str | None = None, level: str | None = None
) -> list[sqlite3.Row]:
    """The newest `limit` events matching the filters, oldest first."""
    conditions, params = [], []
    if command:
        conditions.append("command = ?")
        params.append(command)
    if level:
        conditions.append("level = ?")
        params.append(level)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT at, command, level, filename, message FROM events {where} ORDER BY id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return rows[::-1]
