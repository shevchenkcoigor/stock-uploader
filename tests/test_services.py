from pathlib import Path

import pytest

from adobe_stock import caption, services, sftp_upload, state
from adobe_stock.settings import Settings
from tests.test_caption import fake_claude_ok, make_unlabelled_folder
from tests.test_state import make_folder


def scanned(tmp_path: Path, names: list[str], *, labelled: bool = True):
    db = tmp_path / "s.db"
    folder = make_folder(tmp_path, "f1", names) if labelled else make_unlabelled_folder(tmp_path, names)
    conn = state.open_db(db)
    services.scan(conn, folder.resolve(), services.find_images(folder), None, 1000)
    return db, conn


def test_scan_report_lists_invalid_files(tmp_path):
    folder = make_folder(tmp_path, "f1", ["a.jpg"])
    (folder / "bad.jpg").write_bytes(b"not a jpeg")
    conn = state.open_db(tmp_path / "s.db")
    report = services.scan(conn, folder.resolve(), services.find_images(folder), None, 1000)
    assert report.result.added == 1 and report.result.invalid == 1
    assert [Path(path).name for path, _ in report.invalid] == ["bad.jpg"]


def test_caption_files_reports_progress_and_can_be_cancelled(tmp_path, monkeypatch):
    _, conn = scanned(tmp_path, [f"{i}.jpg" for i in range(4)], labelled=False)
    monkeypatch.setattr(caption, "run_claude", fake_claude_ok)
    progress: list[tuple[int, int]] = []

    def on_progress(done: int, total: int, message: str) -> None:
        progress.append((done, total))

    report = services.caption_files(
        conn,
        "claude-haiku-5-5",
        per_call=1,
        on_progress=on_progress,
        should_stop=lambda: len(progress) >= 2,
    )
    assert report.stopped and report.total == 4 and report.captioned == 2
    assert progress == [(1, 4), (2, 4)]
    assert len(state.files_needing_metadata(conn, 10)) == 2  # the rest is still waiting


def test_caption_files_without_work_returns_an_empty_report(tmp_path):
    _, conn = scanned(tmp_path, ["a.jpg"])
    assert services.caption_files(conn, "claude-haiku-5-5").total == 0


def test_upload_batch_reports_progress_failures_and_cancellation(tmp_path, monkeypatch):
    _, conn = scanned(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    messages: list[str] = []

    def fake_upload(settings, files, on_success, on_failure, should_stop):
        on_success(files[0][0])
        on_failure(files[1][1], "disk full")
        assert should_stop()  # the caller asked to stop before the third file
        return [files[1][1]]

    monkeypatch.setattr(services, "upload_files", fake_upload)
    report = services.upload_batch(
        conn,
        1,
        Settings(),
        on_progress=lambda done, total, message: messages.append(f"{done}/{total} {message}"),
        should_stop=lambda: len(messages) >= 2,
    )
    assert (report.uploaded, report.failed, report.stopped) == (1, ("b.jpg",), True)
    assert messages == ["1/3 uploaded a.jpg", "2/3 FAILED b.jpg: disk full"]
    assert [p.name for p, _ in state.pending_files(conn)] == ["b.jpg", "c.jpg"]
    last = state.recent_events(conn, 10, command="upload")
    assert any(e["message"] == "disk full" and e["filename"] == "b.jpg" for e in last)


def test_upload_batch_turns_connection_errors_into_a_report(tmp_path, monkeypatch):
    _, conn = scanned(tmp_path, ["a.jpg"])

    def refuse(*_args, **_kwargs):
        raise OSError("network down")

    monkeypatch.setattr(services, "upload_files", refuse)
    report = services.upload_batch(conn, 1, Settings())
    assert report.error and "network down" in report.error and not report.ok
    assert len(state.pending_files(conn)) == 1


class FakeClient:
    def __init__(self, fail_names: set[str]):
        self.fail_names = fail_names
        self.put_names: list[str] = []

    def put(self, _local: str, remote: str) -> None:
        if remote in self.fail_names:
            raise OSError("quota exceeded")
        self.put_names.append(remote)


def patch_paramiko(monkeypatch, client: FakeClient) -> None:
    class FakeTransport:
        def __init__(self, *_address):
            pass

        def connect(self, **_credentials):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sftp_upload.paramiko, "Transport", FakeTransport)
    monkeypatch.setattr(sftp_upload.paramiko.SFTPClient, "from_transport", staticmethod(lambda _t: client))


def test_upload_files_calls_back_stops_and_collects_failures(monkeypatch):
    client = FakeClient({"b.jpg"})
    patch_paramiko(monkeypatch, client)
    settings = Settings(sftp_user="u", sftp_password="p")  # type: ignore[arg-type]
    files = [(Path(name), name) for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg")]
    succeeded: list[Path] = []
    failures: list[tuple[str, str]] = []

    failed = sftp_upload.upload_files(
        settings,
        files,
        on_success=succeeded.append,
        on_failure=lambda name, error: failures.append((name, error)),
        should_stop=lambda: len(succeeded) + len(failures) >= 3,  # stop before d.jpg
    )
    assert failed == ["b.jpg"] and failures == [("b.jpg", "quota exceeded")]
    assert [p.name for p in succeeded] == ["a.jpg", "c.jpg"]
    assert client.put_names == ["a.jpg", "c.jpg"]


def test_protected_blocks_a_vanished_database_and_backs_up_mutations(tmp_path):
    db, _ = scanned(tmp_path, ["a.jpg"])
    with services.protected(db):
        pass
    backups = list((tmp_path / "s_backups").glob("*"))
    assert backups  # snapshot taken before and after

    db.unlink()
    with pytest.raises(services.StateDbError, match="restore"), services.protected(db):
        pass


def test_build_csv_report_covers_empty_ok_and_rewrite(tmp_path):
    db, conn = scanned(tmp_path, ["a.jpg"])
    settings = Settings(csv_dir=tmp_path / "csv_out")
    assert services.build_csv(conn, 1, settings).empty  # nothing uploaded yet

    state.mark_uploaded(conn, state.pending_files(conn)[0][0])
    first = services.build_csv(conn, 1, settings)
    assert first.ok and first.rows == 1 and first.path and first.notes
    assert first.path.parent == tmp_path / "csv_out"
    again = services.build_csv(conn, 1, settings)
    assert again.path == first.path  # same file rewritten, not a new one


def test_mark_submitted_warns_without_csv(tmp_path):
    _, conn = scanned(tmp_path, ["a.jpg"])
    state.mark_uploaded(conn, state.pending_files(conn)[0][0])
    report = services.mark_submitted(conn, 1)
    assert report.count == 1 and "no CSV" in report.warnings[0]


def test_approve_releases_flagged_files(tmp_path):
    _, conn = scanned(tmp_path, ["a.jpg"], labelled=False)
    path = state.files_needing_metadata(conn, 10)[0]
    state.set_metadata(conn, path, "T", ("k",), review_reason="looks like a logo")
    assert state.pending_files(conn) == []
    report = services.approve(conn, None, 1000)
    assert report.count == 1 and report.new_batches == 1
    assert len(state.pending_files(conn)) == 1
