import json
from pathlib import Path

import pytest
from PIL import Image

from adobe_stock import caption, state
from adobe_stock.cli import main
from tests.test_state import make_folder


def run(db: Path, *args: str) -> int:
    return main(["--db", str(db), *args])


def envelope(items: list[dict]) -> str:
    return json.dumps({"is_error": False, "structured_output": {"items": items}})


def fake_claude_ok(command, cwd):
    """Answer like Claude: one item per preview file in the working directory."""
    names = sorted(p.name for p in Path(cwd).glob("*.jpg"))
    return envelope(
        [
            {"file": n, "title": f"A title for {n}", "keywords": ["Red", "red", "AI generated", "shape"]}
            for n in names
        ]
    )


def make_unlabelled_folder(tmp_path: Path, files: list[str]) -> Path:
    folder = make_folder(tmp_path, "f1", files)
    (folder / "metadata.csv").unlink()
    return folder


def test_clean_keywords_dedupes_lowercases_and_drops_ai_labels():
    result = caption.clean_keywords(["Red", " red ", "AI generated", "Midjourney", "big  shape", ""])
    assert result == ("red", "big shape")


def test_clean_title_truncates_on_a_word_boundary():
    title = caption.clean_title("word " * 100)
    assert len(title) <= 200 and not title.endswith(" ")


def test_caption_group_maps_results_back_to_sources(tmp_path, monkeypatch):
    sources = []
    for index in range(2):
        path = tmp_path / f"src{index}.jpg"
        Image.new("RGB", (3000, 2000), (index * 100, 0, 0)).save(path, "JPEG")
        sources.append(path)
    monkeypatch.setattr(caption, "run_claude", fake_claude_ok)

    results = caption.caption_group(sources)
    assert [r.path for r in results] == sources
    assert all(r.error is None and r.keywords == ("red", "shape") for r in results)


def test_category_code_from_claude_is_kept_and_invalid_ones_are_dropped(tmp_path, monkeypatch):
    sources = []
    for index in range(3):
        path = tmp_path / f"src{index}.jpg"
        Image.new("RGB", (2000, 2000), (index * 100, 0, 0)).save(path, "JPEG")
        sources.append(path)

    def with_categories(command, cwd):
        names = sorted(p.name for p in Path(cwd).glob("*.jpg"))
        categories = [7, 99, "food"]  # valid, outside Adobe's list, wrong type
        return envelope(
            [
                {"file": n, "title": "T", "keywords": ["k"], "category": c}
                for n, c in zip(names, categories, strict=True)
            ]
        )

    monkeypatch.setattr(caption, "run_claude", with_categories)
    results = caption.caption_group(sources)
    assert [r.category for r in results] == ["7", "", ""]


def test_schema_and_prompt_offer_exactly_adobes_categories():
    from adobe_stock.categories import CATEGORIES

    enum = caption.RESPONSE_SCHEMA["properties"]["items"]["items"]["properties"]["category"]["enum"]
    assert enum == list(CATEGORIES) and len(enum) == 21
    assert "7 Food" in caption.SYSTEM_PROMPT and "21 Travel" in caption.SYSTEM_PROMPT


def test_caption_command_stores_the_category(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_unlabelled_folder(tmp_path, ["a.jpg"])))

    def food(command, cwd):
        names = sorted(p.name for p in Path(cwd).glob("*.jpg"))
        return envelope([{"file": n, "title": "T", "keywords": ["k"], "category": 7} for n in names])

    monkeypatch.setattr(caption, "run_claude", food)
    assert run(db, "caption") == 0
    row = state.open_db(db).execute("SELECT category FROM files").fetchone()
    assert row["category"] == "7"


def test_preview_is_downscaled(tmp_path):
    source = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000)).save(source, "JPEG")
    caption.make_preview(source, tmp_path / "p.jpg")
    assert max(Image.open(tmp_path / "p.jpg").size) <= caption.PREVIEW_MAX_SIDE


def test_caption_command_fills_metadata_and_creates_batches(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    folder = make_unlabelled_folder(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    run(db, "scan", str(folder))
    conn = state.open_db(db)
    assert state.pending_files(conn) == []  # nothing batched before captioning

    monkeypatch.setattr(caption, "run_claude", fake_claude_ok)
    assert run(db, "caption", "--per-call", "2") == 0

    assert sorted(p.name for p, _ in state.pending_files(conn)) == ["a.jpg", "b.jpg", "c.jpg"]
    assert state.pending_batches(conn)[0]["n"] == 3
    row = conn.execute("SELECT title, keywords FROM files WHERE filename = 'a.jpg'").fetchone()
    assert row["title"].startswith("A title") and json.loads(row["keywords"]) == ["red", "shape"]


def test_missing_item_stays_waiting_and_records_error(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_unlabelled_folder(tmp_path, ["a.jpg", "b.jpg"])))

    def only_first(command, cwd):
        names = sorted(p.name for p in Path(cwd).glob("*.jpg"))
        return envelope([{"file": names[0], "title": "T", "keywords": ["k"]}])

    monkeypatch.setattr(caption, "run_claude", only_first)
    assert run(db, "caption") == 1
    conn = state.open_db(db)
    waiting = conn.execute("SELECT error FROM files WHERE status = 'needs_metadata'").fetchall()
    assert len(waiting) == 1 and "nothing" in waiting[0]["error"]


def test_repeated_call_failures_stop_the_run(tmp_path, monkeypatch, capsys):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_unlabelled_folder(tmp_path, [f"{i}.jpg" for i in range(6)])))
    calls = []

    def always_fail(command, cwd):
        calls.append(1)
        raise caption.ClaudeCallError("usage limit reached")

    monkeypatch.setattr(caption, "run_claude", always_fail)
    assert run(db, "caption", "--per-call", "1") == 1
    assert len(calls) == 2  # gave up after two failures instead of trying all six
    assert "usage limit" in capsys.readouterr().out


def test_pick_model_enter_number_and_id():
    from adobe_stock.cli import pick_model

    assert pick_model(ask=lambda _p: "") == caption.DEFAULT_MODEL
    assert pick_model(ask=lambda _p: "2") == caption.MODEL_IDS[1]
    assert pick_model(ask=lambda _p: "claude-opus-5-5") == "claude-opus-5-5"
    with pytest.raises(SystemExit):
        pick_model(ask=lambda _p: "gpt-4")


def test_unknown_model_flag_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path / "s.db", "caption", "--model", "gpt-4")


def test_parse_items_rejects_garbage():
    with pytest.raises(caption.ClaudeCallError):
        caption.parse_items("not json")
    with pytest.raises(caption.ClaudeCallError):
        caption.parse_items(json.dumps({"is_error": True, "result": "rate limited"}))
