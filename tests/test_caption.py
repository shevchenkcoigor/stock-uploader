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


def make_policy_claude(flags):
    """Fake Claude answering per preview file; `flags` maps the n-th file to its policy fields."""

    def fake(command, cwd):
        names = sorted(p.name for p in Path(cwd).glob("*.jpg"))
        items = []
        for index, name in enumerate(names):
            fields = {
                "people_visible": False,
                "content_type": "illustration",
                "review_reason": "",
                **flags.get(index, {}),
            }
            items.append({"file": name, "title": "T", "keywords": ["k"], "category": 8, **fields})
        return envelope(items)

    return fake


def test_schema_and_prompt_carry_adobes_ai_policy():
    props = caption.RESPONSE_SCHEMA["properties"]["items"]["items"]["properties"]
    assert {"people_visible", "content_type", "review_reason"} <= set(props)
    assert props["content_type"]["enum"] == ["photo", "illustration"]
    prompt = caption.SYSTEM_PROMPT.lower()
    for forbidden in ("artists", "fictional characters", "government agencies", "newsworthy"):
        assert forbidden in prompt


def test_flagged_file_is_held_back_until_approved(tmp_path, monkeypatch, capsys):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_unlabelled_folder(tmp_path, ["a.jpg", "b.jpg"])))
    conn = state.open_db(db)
    flags = {1: {"review_reason": "looks like a famous cartoon character"}}
    monkeypatch.setattr(caption, "run_claude", make_policy_claude(flags))

    assert run(db, "caption") == 0
    assert "famous cartoon character" in capsys.readouterr().out
    assert [p.name for p, _ in state.pending_files(conn)] == ["a.jpg"]  # b.jpg is held back
    held = state.review_files(conn)
    assert [r["filename"] for r in held] == ["b.jpg"]

    assert run(db, "review") == 0
    assert "b.jpg" in capsys.readouterr().out

    assert run(db, "approve") == 2  # needs names or --all
    assert run(db, "approve", "b.jpg") == 0
    assert sorted(p.name for p, _ in state.pending_files(conn)) == ["a.jpg", "b.jpg"]
    assert state.review_files(conn) == []


def test_csv_writes_portal_checklist_with_people_and_types(tmp_path, monkeypatch, capsys):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_unlabelled_folder(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])))
    flags = {0: {"people_visible": True, "content_type": "photo"}, 1: {"content_type": "photo"}}
    monkeypatch.setattr(caption, "run_claude", make_policy_claude(flags))
    run(db, "caption")
    conn = state.open_db(db)
    for path, _ in state.pending_files(conn):
        state.mark_uploaded(conn, path)

    out = tmp_path / "csv_out" / "m.csv"
    assert run(db, "csv", "1", "--output", str(out)) == 0
    text = (tmp_path / "csv_out" / "m_portal_notes.txt").read_text(encoding="utf-8")
    assert "Created using generative AI tools: tick for ALL 3 files" in text
    assert "People and Property are fictional: tick for the 1 files" in text
    assert "   a.jpg" in text  # the file with people
    assert "2 photo-realistic, 1 illustrations" in text
    assert "ALL 3 files" in capsys.readouterr().out


def test_old_database_without_new_columns_is_upgraded(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE batches (id INTEGER PRIMARY KEY, folder TEXT NOT NULL, part INTEGER NOT NULL, "
        "csv_path TEXT, created_at TEXT NOT NULL, UNIQUE (folder, part));"
        "CREATE TABLE files (id INTEGER PRIMARY KEY, batch_id INTEGER, path TEXT NOT NULL UNIQUE, "
        "filename TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, title TEXT NOT NULL "
        "DEFAULT '', keywords TEXT NOT NULL DEFAULT '[]', category TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL, error TEXT, updated_at TEXT NOT NULL);"
    )
    conn.close()
    columns = {r["name"] for r in state.open_db(db).execute("PRAGMA table_info(files)")}
    assert {"mtime_ns", "root", "people_visible", "content_type"} <= columns


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Generative AI red apple on table", "red apple on table"),
        ("AI-generated landscape with mountains", "landscape with mountains"),
        ("Mountain lake, Midjourney style", "Mountain lake, style"),
        ("Artificial intelligence robot", "robot"),
        ("Fair weather over a maiden voyage", "Fair weather over a maiden voyage"),  # no false hits
    ],
)
def test_clean_title_removes_ai_wording(raw, expected):
    assert caption.clean_title(raw) == expected


def test_clean_keywords_drops_every_ai_term_but_not_lookalikes():
    kept = caption.clean_keywords(["AI", "generative art", "dall-e 3", "Firefly", "paint", "maiden", "air"])
    assert kept == ("paint", "maiden", "air")


def test_prompt_forbids_ai_wording_in_titles():
    for term in ("'AI'", "'generative'", "'Midjourney'", "'Firefly'"):
        assert term in caption.SYSTEM_PROMPT


def test_title_that_is_only_ai_wording_is_reported(tmp_path, monkeypatch):
    sources = []
    path = tmp_path / "s.jpg"
    Image.new("RGB", (2000, 2000), (9, 9, 9)).save(path, "JPEG")
    sources.append(path)

    def only_ai(command, cwd):
        names = sorted(p.name for p in Path(cwd).glob("*.jpg"))
        return envelope([{"file": n, "title": "AI generated", "keywords": ["k"]} for n in names])

    monkeypatch.setattr(caption, "run_claude", only_ai)
    [result] = caption.caption_group(sources)
    assert result.error == "empty title or keywords"


def test_caption_stores_titles_with_the_label_and_the_csv_keeps_them_as_they_are(tmp_path, monkeypatch):
    import csv as csv_module

    db = tmp_path / "s.db"
    run(db, "scan", str(make_unlabelled_folder(tmp_path, ["a.jpg"])))
    monkeypatch.setattr(caption, "run_claude", fake_claude_ok)
    run(db, "caption")
    conn = state.open_db(db)
    stored = conn.execute("SELECT title FROM files").fetchone()["title"]
    assert stored.startswith("A title") and stored.endswith(" Generative AI")
    for path, _ in state.pending_files(conn):
        state.mark_uploaded(conn, path)

    out = tmp_path / "out.csv"
    assert run(db, "csv", "1", "--output", str(out)) == 0
    title = next(csv_module.DictReader(out.open(encoding="utf-8")))["Title"]
    assert title == stored
    assert not title.endswith("Generative AI Generative AI")


def test_caption_without_a_label_leaves_the_title_alone(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    run(db, "scan", str(make_unlabelled_folder(tmp_path, ["a.jpg"])))
    monkeypatch.setattr(caption, "run_claude", fake_claude_ok)
    monkeypatch.setenv("ADOBE_AI_TITLE_LABEL", "")
    run(db, "caption")
    stored = state.open_db(db).execute("SELECT title FROM files").fetchone()["title"]
    assert not stored.endswith("AI")


def test_clean_keywords_splits_commas_and_drops_numbers_and_file_info():
    kept = caption.clean_keywords(
        ["London, England", "red; blue", "2024", "1920 x 1080", "photo.jpg", "24 mp", "3d render", "red"]
    )
    assert kept == ("london", "england", "red", "blue", "3d render")


def test_prompt_has_the_keyword_rules():
    prompt = caption.SYSTEM_PROMPT.lower()
    for phrase in (
        "one concept",
        "comma or semicolon",
        "main subject first",
        "first 10 keywords",
        "no numbers",
        "add the country",
        "each keyword once",
    ):
        assert phrase in prompt
