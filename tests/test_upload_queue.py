import sqlite3

from adobe_stock import state
from adobe_stock.gui import upload_queue as queue
from adobe_stock.gui.upload_page import progress_text, summary_text


def db_rows(*specs: tuple[str, str, int, str | None]) -> list[sqlite3.Row]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE f (filename TEXT, status TEXT, size INTEGER, error TEXT, path TEXT)")
    conn.executemany("INSERT INTO f VALUES (?, ?, ?, ?, ?)", [(*spec, f"/pics/{spec[0]}") for spec in specs])
    return conn.execute("SELECT * FROM f").fetchall()


MB = 1024 * 1024


def test_rows_tell_done_failed_sending_and_queued_apart():
    files = db_rows(
        ("a.jpg", state.UPLOADED, MB, None),
        ("b.jpg", state.PENDING, MB, "timeout"),
        ("c.jpg", state.PENDING, MB, None),
        ("d.jpg", state.PENDING, MB, None),
    )

    rows = queue.rows_from_db(files, running=True)

    assert [row.state for row in rows] == [queue.DONE, queue.FAILED, queue.UPLOADING, queue.QUEUED]
    assert [row.progress for row in rows] == [1.0, 0.0, 0.0, 0.0]


def test_nothing_is_sending_while_no_run_is_on():
    files = db_rows(("c.jpg", state.PENDING, MB, None))

    assert [row.state for row in queue.rows_from_db(files, running=False)] == [queue.QUEUED]


def test_totals_count_files_errors_and_bytes():
    rows = [
        queue.UploadRow("a.jpg", 2 * MB, queue.DONE, 1.0),
        queue.UploadRow("b.jpg", 2 * MB, queue.UPLOADING, 0.5),
        queue.UploadRow("c.jpg", 4 * MB, queue.FAILED, 0.0),
    ]

    totals = queue.totals_of(rows)

    assert (totals.total, totals.done, totals.failed) == (3, 1, 1)
    assert totals.fraction == 3 * MB / (8 * MB)


def test_totals_of_an_empty_queue_have_zero_progress():
    assert queue.totals_of([]).fraction == 0.0


def test_status_sort_puts_errors_first_and_done_last():
    rows = [
        queue.UploadRow("done.jpg", 1, queue.DONE, 1.0),
        queue.UploadRow("wait.jpg", 1, queue.QUEUED, 0.0),
        queue.UploadRow("err.jpg", 1, queue.FAILED, 0.0),
        queue.UploadRow("busy.jpg", 1, queue.UPLOADING, 0.4),
    ]

    names = [row.filename for row in queue.sorted_rows(rows, "status", descending=False)]

    assert names == ["err.jpg", "busy.jpg", "wait.jpg", "done.jpg"]


def test_size_sort_can_be_reversed():
    rows = [queue.UploadRow("a", 3, queue.QUEUED, 0.0), queue.UploadRow("b", 9, queue.QUEUED, 0.0)]

    assert [row.filename for row in queue.sorted_rows(rows, "size", descending=True)] == ["b", "a"]


def test_summary_and_progress_texts_match_the_design():
    totals = queue.QueueTotals(total=6, done=2, failed=1, sent_bytes=9.3 * MB, total_bytes=int(19.8 * MB))

    assert summary_text(totals) == "2 из 6 загружено · 1 ошибка"
    assert progress_text(totals) == "47% · 9,3 из 19,8 МБ"
    assert summary_text(queue.QueueTotals(5, 1, 3, 0, 1)).endswith("3 ошибки")
    assert summary_text(queue.QueueTotals(5, 1, 5, 0, 1)).endswith("5 ошибок")


def test_rows_carry_the_picture_path_for_the_preview():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE f (path TEXT, filename TEXT, status TEXT, size INTEGER, error TEXT)")
    conn.execute("INSERT INTO f VALUES ('/pics/a.jpg', 'a.jpg', 'pending', 5, NULL)")

    rows = queue.rows_from_db(conn.execute("SELECT * FROM f").fetchall(), running=False)

    assert rows[0].path == "/pics/a.jpg"
