"""Backups and integrity checks for the state database (our only source of truth)."""

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from adobe_stock.settings import load_settings

DEFAULT_KEEP = 30
BACKUP_SUFFIX = ".bak"
READ_ONLY = 0o444


def backup_dir_for(db_path: Path) -> Path:
    """Default is a sibling folder; set ADOBE_BACKUP_DIR to keep copies on another disk."""
    override = load_settings().backup_dir
    return override if override else db_path.parent / f"{db_path.stem}_backups"


def list_backups(backup_dir: Path, stem: str) -> list[Path]:
    """Backups of this database, oldest first (timestamped names sort chronologically)."""
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob(f"{stem}_*{BACKUP_SUFFIX}"))


def create_backup(db_path: Path, backup_dir: Path, keep: int = DEFAULT_KEEP) -> Path:
    """Copy the live database with SQLite's online backup API, then prune old copies."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    target = backup_dir / f"{db_path.stem}_{stamp}{BACKUP_SUFFIX}"
    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(target)
    try:
        with destination:
            source.backup(destination)
    finally:
        source.close()
        destination.close()
    target.chmod(READ_ONLY)
    for old in list_backups(backup_dir, db_path.stem)[:-keep]:
        old.unlink()
    return target


def integrity_ok(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def restore_latest(db_path: Path, backup_dir: Path) -> Path:
    """Restore the newest backup that passes the integrity check."""
    for candidate in reversed(list_backups(backup_dir, db_path.stem)):
        if integrity_ok(candidate):
            shutil.copyfile(candidate, db_path)
            db_path.chmod(0o644)
            return candidate
    raise FileNotFoundError(f"no valid backup in {backup_dir}")
