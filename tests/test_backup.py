import pytest

from adobe_stock import backup, state
from adobe_stock.cli import main
from tests.test_state import make_folder


def run(db, *args):
    return main(["--db", str(db), *args])


def test_mutating_command_creates_backups(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    backups = backup.list_backups(backup.backup_dir_for(db), "s")
    assert len(backups) >= 1
    assert backups[-1].stat().st_mode & 0o222 == 0  # read-only


def test_old_backups_are_pruned(tmp_path):
    db = tmp_path / "s.db"
    state.open_db(db).close()
    for _ in range(5):
        backup.create_backup(db, tmp_path / "b", keep=3)
    assert len(backup.list_backups(tmp_path / "b", "s")) == 3


def test_missing_db_with_backups_refuses_to_start_empty(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    db.unlink()
    with pytest.raises(SystemExit, match="restore"):
        run(db, "status")


def test_restore_recovers_state(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    db.unlink()
    assert run(db, "restore") == 0
    assert len(state.pending_files(state.open_db(db))) == 1


def test_corrupt_db_is_detected(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    db.write_bytes(b"garbage" * 1000)
    with pytest.raises(SystemExit, match="integrity"):
        run(db, "status")
    assert run(db, "restore", "--force") == 0
    assert state.pending_files(state.open_db(db))
