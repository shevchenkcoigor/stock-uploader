from pathlib import Path

from adobe_stock import state
from adobe_stock.cli import main
from tests.test_state import make_folder


def run(db: Path, *args: str) -> int:
    return main(["--db", str(db), *args])


def titles(db: Path) -> list[str]:
    return [row["title"] for row in state.open_db(db).execute("SELECT title FROM files ORDER BY id")]


def test_label_titles_appends_the_label_to_titles_without_it(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg", "b.jpg"])))
    before = titles(db)

    assert run(db, "label-titles", "Generative AI") == 0

    assert all(after == f"{old} Generative AI" for old, after in zip(before, titles(db), strict=True))


def test_label_titles_skips_titles_that_already_have_the_label(tmp_path):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    run(db, "label-titles", "Generative AI")
    once = titles(db)

    run(db, "label-titles", "Generative AI")

    assert titles(db) == once


def test_label_titles_uses_the_setting_when_no_label_is_given(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_folder(tmp_path, "f1", ["a.jpg"])))
    monkeypatch.setenv("ADOBE_AI_TITLE_LABEL", "From settings")

    run(db, "label-titles")

    assert titles(db)[0].endswith(" From settings")


def test_label_titles_leaves_files_without_a_title_alone(tmp_path):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", ["a.jpg"])
    (folder / "metadata.csv").unlink()
    run(db, "scan", str(folder))

    run(db, "label-titles", "Generative AI")

    assert titles(db) == [""]
