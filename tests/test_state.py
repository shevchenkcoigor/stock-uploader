from pathlib import Path

import pytest
from PIL import Image

from adobe_stock import state
from adobe_stock.cli import main
from adobe_stock.metadata_csv import Item


def make_folder(root: Path, name: str, files: list[str], shade: int = 0) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for index, file_name in enumerate(files):
        Image.new("RGB", (2000, 2000), ((index * 40 + shade) % 256, 0, 0)).save(folder / file_name, "JPEG")
    rows = "".join(f"{f},Title {f},\"a, b\"\n" for f in files)
    (folder / "metadata.csv").write_text("filename,title,keywords\n" + rows, encoding="utf-8")
    return folder


def run(db: Path, *args: str) -> int:
    return main(["--db", str(db), *args])


def test_scan_is_incremental(tmp_path):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", ["a.jpg", "b.jpg"])
    assert run(db, "scan", str(folder)) == 0
    conn = state.open_db(db)
    assert len(state.pending_files(conn)) == 2

    # Rescan with no changes creates nothing new.
    run(db, "scan", str(folder))
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1

    # A new file in the same folder becomes a second batch part.
    Image.new("RGB", (2000, 2000), (200, 200, 200)).save(folder / "c.jpg", "JPEG")
    (folder / "metadata.csv").write_text(
        "filename,title,keywords\na.jpg,T,k\nb.jpg,T,k\nc.jpg,T,k\n", encoding="utf-8"
    )
    run(db, "scan", str(folder))
    assert [r["part"] for r in conn.execute("SELECT part FROM batches ORDER BY part")] == [1, 2]


def test_same_name_different_content_gets_unique_remote_name(tmp_path):
    db = tmp_path / "s.db"
    root = tmp_path / "root"
    make_folder(root, "f1", ["a.jpg"], shade=0)
    make_folder(root, "f2", ["a.jpg"], shade=80)
    run(db, "scan", str(root))
    names = [r["filename"] for r in state.open_db(db).execute("SELECT filename FROM files")]
    assert len(set(names)) == 2
    assert "a.jpg" in names
    assert any(n.startswith("a_") and n.endswith(".jpg") for n in names)


def test_identical_content_in_two_folders_is_invalid_duplicate(tmp_path):
    db = tmp_path / "s.db"
    root = tmp_path / "root"
    make_folder(root, "f1", ["a.jpg"])
    make_folder(root, "f2", ["a.jpg"])
    run(db, "scan", str(root))
    row = state.open_db(db).execute("SELECT error FROM files WHERE status = 'invalid'").fetchone()
    assert "same content" in row["error"]


def test_scan_is_recursive_and_skips_hidden_files(tmp_path):
    db = tmp_path / "s.db"
    root = tmp_path / "root"
    make_folder(root / "deep" / "er", "f1", ["a.jpg"])
    make_folder(root, "f2", ["b.jpg"], shade=40)
    hidden = root / "f2" / "._b.jpg"
    hidden.write_bytes(b"junk")
    run(db, "scan", str(root))
    assert len(state.pending_files(state.open_db(db))) == 2


def test_rescan_only_new_files_reach_the_next_csv(tmp_path):
    db = tmp_path / "s.db"
    root = tmp_path / "root"
    make_folder(root, "old", ["a.jpg"])
    run(db, "scan", str(root))
    conn = state.open_db(db)
    for path, _ in state.pending_files(conn):
        state.mark_uploaded(conn, path)
    state.mark_batch_submitted(conn, 1)

    make_folder(root, "new", ["b.jpg"], shade=60)  # added later to the same root
    run(db, "scan", str(root))
    pending = state.pending_files(conn)
    assert [p.name for p, _ in pending] == ["b.jpg"]  # old file is not offered again
    for path, _ in pending:
        state.mark_uploaded(conn, path)
    assert [i.filename for i in state.batch_items(conn, 2)] == ["b.jpg"]
    assert state.batch_items(conn, 1) == []


def test_invalid_file_is_reevaluated_after_metadata_appears(tmp_path):
    db = tmp_path / "s.db"
    root = tmp_path / "root"
    folder = make_folder(root, "f1", ["a.jpg"])
    (folder / "metadata.csv").unlink()
    run(db, "scan", str(root))
    conn = state.open_db(db)
    assert state.pending_files(conn) == []

    (folder / "metadata.csv").write_text("filename,title,keywords\na.jpg,T,k\n", encoding="utf-8")
    run(db, "scan", str(root))
    assert len(state.pending_files(conn)) == 1


def test_missing_metadata_row_makes_file_invalid(tmp_path):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", ["a.jpg", "b.jpg"])
    (folder / "metadata.csv").write_text("filename,title,keywords\na.jpg,T,k\n", encoding="utf-8")
    run(db, "scan", str(folder))
    conn = state.open_db(db)
    assert conn.execute("SELECT filename FROM files WHERE status='invalid'").fetchone()[0] == "b.jpg"


def test_lifecycle_upload_csv_submit(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    conn = state.open_db(db)
    path = state.pending_files(conn)[0][0]

    assert state.batch_items(conn, 1) == []  # nothing uploaded yet, so no CSV rows
    state.mark_uploaded(conn, path)
    assert [i.filename for i in state.batch_items(conn, 1)] == ["a.jpg"]

    out = tmp_path / "out.csv"
    assert run(db, "csv", "1", "--output", str(out)) == 0
    assert out.exists()

    assert state.mark_batch_submitted(conn, 1) == 1
    assert state.batch_items(conn, 1) == []


def test_csv_is_auto_named_in_csv_dir_and_rewritten_in_place(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    conn = state.open_db(db)
    state.mark_uploaded(conn, state.pending_files(conn)[0][0])
    csv_dir = tmp_path / "csv_out"

    run(db, "csv", "1", "--csv-dir", str(csv_dir))
    run(db, "csv", "1", "--csv-dir", str(csv_dir))
    names = [p.name for p in csv_dir.iterdir()]
    assert len(names) == 1  # second run rewrote the same file
    assert names[0].startswith("metadata_") and names[0].endswith("_batch1.csv")


def test_pick_batch_enter_chooses_last_and_number_chooses_that_batch():
    from adobe_stock.cli import pick_batch

    rows = [
        {"batch_id": 1, "folder": "/x", "part": 1, "n": 5},
        {"batch_id": 2, "folder": "/x", "part": 2, "n": 3},
    ]
    assert pick_batch(rows, ask=lambda _prompt: "") == 2
    assert pick_batch(rows, ask=lambda _prompt: "1") == 1
    with pytest.raises(SystemExit):
        pick_batch(rows, ask=lambda _prompt: "9")


def test_upload_without_batch_and_without_terminal_takes_last_batch_only(tmp_path, monkeypatch):
    from adobe_stock import cli

    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", [f"{i}.jpg" for i in range(4)])
    run(db, "scan", str(folder), "--batch-size", "2")

    uploaded: list[str] = []

    def fake_upload(_config, files, on_success):
        for path, remote in files:
            uploaded.append(remote)
            on_success(path)
        return []

    monkeypatch.setattr(cli, "upload_files", fake_upload)
    monkeypatch.setattr(cli.SftpConfig, "from_env", classmethod(lambda cls: None))

    run(db, "upload")  # pytest has no TTY, so the last pending batch is used
    assert sorted(uploaded) == ["2.jpg", "3.jpg"]
    run(db, "upload")
    assert sorted(uploaded) == ["0.jpg", "1.jpg", "2.jpg", "3.jpg"]
    assert state.pending_batches(state.open_db(db)) == []


def test_status_shows_csv_link_and_submitted_warns_without_csv(tmp_path, capsys):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    conn = state.open_db(db)
    state.mark_uploaded(conn, state.pending_files(conn)[0][0])

    run(db, "status")
    assert "csv: -" in capsys.readouterr().out

    run(db, "csv", "1", "--csv-dir", str(tmp_path / "csv_out"))
    run(db, "status")
    assert "_batch1.csv" in capsys.readouterr().out

    run(db, "submitted", "1")
    assert "WARNING" not in capsys.readouterr().out


def test_submitted_warns_when_batch_has_no_csv(tmp_path, capsys):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    state.mark_uploaded(state.open_db(db), state.pending_files(state.open_db(db))[0][0])
    run(db, "submitted", "1")
    assert "has no CSV" in capsys.readouterr().out


def test_scan_batch_size_splits_folder(tmp_path):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", [f"{i}.jpg" for i in range(5)])
    run(db, "scan", str(folder), "--batch-size", "2")
    sizes = [r[0] for r in state.open_db(db).execute(
        "SELECT COUNT(*) FROM files GROUP BY batch_id ORDER BY batch_id")]
    assert sizes == [2, 2, 1]


def test_batch_size_above_adobe_limit_is_rejected(tmp_path):
    folder = make_folder(tmp_path, "f1", ["a.jpg"])
    with pytest.raises(SystemExit):
        run(tmp_path / "s.db", "scan", str(folder), "--batch-size", "5001")


def test_large_set_splits_into_several_batches(monkeypatch):
    from adobe_stock import metadata_csv

    monkeypatch.setattr(metadata_csv, "MAX_ROWS", 2)
    items = [Item(f"{i}.jpg", "t", ("k",)) for i in range(5)]
    assert [len(g) for g in metadata_csv.split_into_batches(items)] == [2, 2, 1]
