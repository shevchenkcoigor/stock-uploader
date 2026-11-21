from pathlib import Path

from adobe_stock import csv_batches, services, state
from adobe_stock.settings import Settings
from tests.test_services import scanned


def uploaded(tmp_path: Path, names: list[str]):
    _, conn = scanned(tmp_path, names)
    for path, _ in state.pending_files(conn):
        state.mark_uploaded(conn, path)
    return conn


def settings(tmp_path: Path) -> Settings:
    return Settings(csv_dir=tmp_path / "csv_out")


def test_batch_without_csv_is_offered_but_not_listed(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg", "b.jpg"])

    assert csv_batches.csv_entries(conn) == []
    offered = csv_batches.batches_without_csv(conn)
    assert [row["n"] for row in offered] == [2]


def test_built_csv_is_listed_as_built_with_its_rows(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg", "b.jpg"])
    batch = csv_batches.batches_without_csv(conn)[0]["batch_id"]
    services.build_csv(conn, batch, settings(tmp_path))

    [entry] = csv_batches.csv_entries(conn)

    assert (entry.state, entry.rows, entry.bad_rows) == (csv_batches.BUILT, 2, 0)
    assert entry.name.endswith(".csv")
    assert csv_batches.batches_without_csv(conn) == []
    assert sorted(r["filename"] for r in csv_batches.csv_rows(conn, batch)) == ["a.jpg", "b.jpg"]


def test_entry_carries_the_csv_file_times(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg"])
    batch = csv_batches.batches_without_csv(conn)[0]["batch_id"]
    services.build_csv(conn, batch, settings(tmp_path))

    [entry] = csv_batches.csv_entries(conn)

    assert 0 < entry.created <= entry.updated


def test_missing_csv_file_has_no_times(tmp_path):
    assert csv_batches.file_times(tmp_path / "nope.csv") == (0.0, 0.0)


def test_row_without_title_makes_the_csv_an_error(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg", "b.jpg"])
    batch = csv_batches.batches_without_csv(conn)[0]["batch_id"]
    services.build_csv(conn, batch, settings(tmp_path))
    state.update_metadata(conn, "a.jpg", "", ("sea",), "")

    [entry] = csv_batches.csv_entries(conn)

    assert (entry.state, entry.bad_rows) == (csv_batches.ERROR, 1)


def test_missing_csv_file_is_an_error(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg"])
    batch = csv_batches.batches_without_csv(conn)[0]["batch_id"]
    report = services.build_csv(conn, batch, settings(tmp_path))
    assert report.path is not None
    report.path.unlink()

    assert csv_batches.csv_entries(conn)[0].state == csv_batches.ERROR


def test_submitted_batch_is_sent_and_keeps_its_rows(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg"])
    batch = csv_batches.batches_without_csv(conn)[0]["batch_id"]
    services.build_csv(conn, batch, settings(tmp_path))
    services.mark_submitted(conn, batch)

    [entry] = csv_batches.csv_entries(conn)

    assert (entry.state, entry.rows) == (csv_batches.SENT, 1)
    assert [r["status"] for r in csv_batches.csv_rows(conn, batch)] == [state.SUBMITTED]


def test_entry_names_its_batch_by_folder_and_part(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg"])
    batch = csv_batches.batches_without_csv(conn)[0]["batch_id"]
    services.build_csv(conn, batch, settings(tmp_path))

    [entry] = csv_batches.csv_entries(conn)

    assert entry.batch_name == "f1 · 1"
    assert entry.waiting == ()


def test_batch_with_files_not_uploaded_yet_lists_what_is_missing(tmp_path):
    _, conn = scanned(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    first = state.pending_files(conn)[0][0]
    state.mark_uploaded(conn, first)
    services.build_csv(conn, 1, settings(tmp_path))

    [entry] = csv_batches.csv_entries(conn)

    assert entry.waiting == ((state.PENDING, 2),)


def test_mark_submitted_is_refused_until_the_whole_batch_is_uploaded(tmp_path):
    _, conn = scanned(tmp_path, ["a.jpg", "b.jpg"])
    state.mark_uploaded(conn, state.pending_files(conn)[0][0])
    services.build_csv(conn, 1, settings(tmp_path))

    refused = services.mark_submitted(conn, 1)

    assert refused.count == 0 and refused.waiting == ((state.PENDING, 1),)
    assert csv_batches.csv_entries(conn)[0].state != csv_batches.SENT

    state.mark_uploaded(conn, state.pending_files(conn)[0][0])
    services.build_csv(conn, 1, settings(tmp_path))
    done = services.mark_submitted(conn, 1)

    assert done.count == 2 and done.waiting == ()


def test_batch_status_counts_per_batch(tmp_path):
    _, conn = scanned(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    state.mark_uploaded(conn, state.pending_files(conn)[0][0])

    assert state.batch_status_counts(conn) == {1: ((state.PENDING, 2), (state.UPLOADED, 1))}


def test_title_with_the_ai_label_is_not_an_error(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg"])
    state.update_metadata(conn, "a.jpg", "Sea at dawn Generative AI", ("sea",), "")
    batch = csv_batches.batches_without_csv(conn)[0]["batch_id"]
    report = services.build_csv(conn, batch, settings(tmp_path))

    [entry] = csv_batches.csv_entries(conn)

    assert report.ok
    assert (entry.state, entry.bad_rows) == (csv_batches.BUILT, 0)


def test_build_csv_reports_row_problems_instead_of_claiming_success(tmp_path):
    conn = uploaded(tmp_path, ["a.jpg"])
    state.update_metadata(conn, "a.jpg", "Sea at dawn Midjourney", ("sea",), "")
    batch = csv_batches.batches_without_csv(conn)[0]["batch_id"]

    report = services.build_csv(conn, batch, settings(tmp_path))

    assert not report.ok
    assert any("Midjourney" in problem for problem in report.problems)
