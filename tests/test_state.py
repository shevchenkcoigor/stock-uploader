from pathlib import Path

from PIL import Image

from adobe_stock import state
from adobe_stock.cli import main
from adobe_stock.metadata_csv import Item


def make_folder(root: Path, name: str, files: list[str]) -> Path:
    folder = root / name
    folder.mkdir()
    for index, file_name in enumerate(files):
        Image.new("RGB", (2000, 2000), (index, 0, 0)).save(folder / file_name, "JPEG")
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
    Image.new("RGB", (2000, 2000), (9, 9, 9)).save(folder / "c.jpg", "JPEG")
    (folder / "metadata.csv").write_text(
        "filename,title,keywords\na.jpg,T,k\nb.jpg,T,k\nc.jpg,T,k\n", encoding="utf-8"
    )
    run(db, "scan", str(folder))
    assert [r["part"] for r in conn.execute("SELECT part FROM batches ORDER BY part")] == [1, 2]


def test_filename_collision_between_folders_is_invalid(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    run(db, "scan", str(make_folder(tmp_path, "f2", ["a.jpg"])))
    conn = state.open_db(db)
    row = conn.execute("SELECT error FROM files WHERE status = 'invalid'").fetchone()
    assert "already used" in row["error"]


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
    path = state.pending_files(conn)[0]

    assert state.batch_items(conn, 1) == []  # nothing uploaded yet, so no CSV rows
    state.mark_uploaded(conn, path)
    assert [i.filename for i in state.batch_items(conn, 1)] == ["a.jpg"]

    out = tmp_path / "out.csv"
    assert run(db, "csv", "1", "--output", str(out)) == 0
    assert out.exists()

    assert state.mark_batch_submitted(conn, 1) == 1
    assert state.batch_items(conn, 1) == []


def test_large_set_splits_into_several_batches(monkeypatch):
    from adobe_stock import metadata_csv

    monkeypatch.setattr(metadata_csv, "MAX_ROWS", 2)
    items = [Item(f"{i}.jpg", "t", ("k",)) for i in range(5)]
    assert [len(g) for g in metadata_csv.split_into_batches(items)] == [2, 2, 1]
