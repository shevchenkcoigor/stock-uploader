from pathlib import Path

from PIL import Image

from adobe_stock import state
from adobe_stock.cli import main
from tests.test_state import make_folder


def run(db: Path, *args: str) -> int:
    return main(["--db", str(db), *args])


def add_files(folder: Path, names: list[str], shade: int) -> None:
    """Add images and list them in the folder's metadata.csv so the next scan describes them."""
    for index, name in enumerate(names):
        Image.new("RGB", (2000, 2000), ((index * 40 + shade) % 256, 0, 0)).save(folder / name, "JPEG")
    csv_file = folder / "metadata.csv"
    rows = "".join(f'{n},Title {n},"a, b"\n' for n in names)
    csv_file.write_text(csv_file.read_text(encoding="utf-8") + rows, encoding="utf-8")


def batch_sizes(db: Path) -> list[int]:
    rows = state.open_db(db).execute(
        "SELECT count(f.id) AS n FROM batches b JOIN files f ON f.batch_id = b.id GROUP BY b.id ORDER BY b.id"
    )
    return [row["n"] for row in rows]


def test_new_files_join_the_open_batch_of_the_same_folder(tmp_path):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", ["a.jpg", "b.jpg"])
    run(db, "scan", str(folder))
    add_files(folder, ["c.jpg", "d.jpg"], shade=100)

    run(db, "scan", str(folder))

    assert batch_sizes(db) == [4]


def test_only_the_overflow_starts_a_new_batch(tmp_path):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", ["a.jpg"])
    run(db, "scan", str(folder), "--batch-size", "2")
    add_files(folder, ["b.jpg", "c.jpg", "d.jpg"], shade=100)

    run(db, "scan", str(folder), "--batch-size", "2")

    assert batch_sizes(db) == [2, 2]


def test_a_batch_with_an_uploaded_file_is_closed(tmp_path):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", ["a.jpg"])
    run(db, "scan", str(folder))
    conn = state.open_db(db)
    state.mark_uploaded(conn, state.pending_files(conn)[0][0])
    add_files(folder, ["b.jpg"], shade=100)

    run(db, "scan", str(folder))

    assert batch_sizes(db) == [1, 1]


def test_a_batch_with_a_csv_is_closed(tmp_path):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", ["a.jpg"])
    run(db, "scan", str(folder))
    conn = state.open_db(db)
    state.record_csv(conn, 1, tmp_path / "out.csv")
    add_files(folder, ["b.jpg"], shade=100)

    run(db, "scan", str(folder))

    assert batch_sizes(db) == [1, 1]


def test_a_batch_of_another_folder_is_not_topped_up(tmp_path):
    db = tmp_path / "s.db"
    first = make_folder(tmp_path, "f1", ["a.jpg"])
    second = make_folder(tmp_path, "f2", ["b.jpg"], shade=100)
    run(db, "scan", str(first))

    run(db, "scan", str(second))

    assert batch_sizes(db) == [1, 1]
