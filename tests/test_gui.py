import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from adobe_stock import caption, services, state  # noqa: E402
from adobe_stock.gui.context import AppContext  # noqa: E402
from adobe_stock.gui.main_window import MainWindow  # noqa: E402
from adobe_stock.gui.prefs import MemoryStore, Preferences  # noqa: E402
from tests.test_caption import make_policy_claude, make_unlabelled_folder  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def prefs(tmp_path):
    store = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    preferences = Preferences(store, MemoryStore("secret"))
    preferences.set_text("db_path", str(tmp_path / "gui.db"))
    preferences.set_text("csv_dir", str(tmp_path / "csv_out"))
    return preferences


@pytest.fixture
def window(app, prefs):
    created = MainWindow(AppContext(prefs))
    yield created
    created.close()


def page(window: MainWindow, title_part: str) -> Any:
    return next(widget for title, widget in window.pages if title_part in title)


def finish(app, current_page: Any) -> None:
    """Wait for the page's background task and deliver its queued signals to the UI thread."""
    task = current_page._task
    assert task is not None, "the page did not start a task"
    task.wait()
    app.processEvents()
    assert current_page._task is None


def test_window_lists_every_step_and_starts_empty(window):
    assert [title for title, _ in window.pages] == [
        "Настройки",
        "1. Скан",
        "2. Подписи",
        "3. Проверка",
        "4. Загрузка",
        "5. CSV и отправка",
        "Журнал",
    ]
    assert "База пуста" in window.summary.text()


def test_preferences_build_settings_from_app_values(prefs):
    prefs.set_text("sftp_host", "sftp://sftp.example.test/")
    prefs.set_text("sftp_port", "2222")
    prefs.set_text("sftp_user", "me")
    settings = prefs.build_settings()
    assert (settings.sftp_host, settings.sftp_port, settings.sftp_user) == ("sftp.example.test", 2222, "me")
    assert settings.sftp_password.get_secret_value() == "secret"
    assert settings.ai_title_label == "Generative AI"
    assert settings.csv_dir.name == "csv_out"


def test_settings_page_saves_values_and_keeps_password_out_of_the_file(app, window, prefs, tmp_path):
    settings_page = page(window, "Настройки")
    settings_page.user.setText("artist")
    settings_page.password.setText("new-secret")
    settings_page.label.setText("")
    assert settings_page._save() is True
    assert prefs.text("sftp_user") == "artist" and prefs.text("ai_title_label") == ""
    assert prefs.secrets.get() == "new-secret"
    assert "new-secret" not in (tmp_path / "prefs.ini").read_text()
    assert settings_page.password.text() == ""

    settings_page.port.setText("not a number")
    assert settings_page._save() is False


def test_full_flow_through_the_pages(app, window, prefs, tmp_path, monkeypatch):
    folder = make_unlabelled_folder(tmp_path, ["a.jpg", "b.jpg"])
    monkeypatch.setattr(caption, "find_claude", lambda: "claude")
    flags = {1: {"review_reason": "looks like a famous logo", "people_visible": False}}
    monkeypatch.setattr(caption, "run_claude", make_policy_claude(flags))

    scan_page = page(window, "Скан")
    scan_page.folder.setText(str(folder))
    scan_page._run()
    finish(app, scan_page)
    assert "Ждут подписи: 2" in scan_page.output.toPlainText()

    caption_page = page(window, "Подписи")
    caption_page._run()
    finish(app, caption_page)
    assert "Подписано: 1 из 2" in caption_page.output.toPlainText()
    assert "famous logo" in caption_page.output.toPlainText()

    review_page = page(window, "Проверка")
    review_page.refresh()
    assert review_page.table.rowCount() == 1
    review_page.table.selectRow(0)
    review_page._approve(selected_only=True)
    finish(app, review_page)
    assert review_page.table.rowCount() == 0

    conn = state.open_db(prefs.db_path)
    for path, _ in state.pending_files(conn):
        state.mark_uploaded(conn, path)
    csv_page = page(window, "CSV")
    csv_page.refresh()
    csv_page._create()
    finish(app, csv_page)
    text = csv_page.output.toPlainText()
    assert "CSV:" in text and "all 2" not in text and "Created using generative AI tools" in text
    assert (tmp_path / "csv_out").exists() and list((tmp_path / "csv_out").glob("*.csv"))
    assert "загружено: 2" in window.summary.text()


def test_upload_page_reports_a_connection_error(app, window, prefs, tmp_path, monkeypatch):
    folder = make_unlabelled_folder(tmp_path, ["a.jpg"])
    services.scan(state.open_db(prefs.db_path), folder.resolve(), services.find_images(folder), None, 1000)
    conn = state.open_db(prefs.db_path)
    state.set_metadata(conn, state.files_needing_metadata(conn, 5)[0], "T", ("k",))
    state.assign_batches(conn, 1000)

    def refuse(*_args, **_kwargs):
        raise OSError("network down")

    monkeypatch.setattr(services, "upload_files", refuse)
    prefs.set_text("sftp_user", "me")  # the password comes from the in-memory store
    upload_page = page(window, "Загрузка")
    upload_page.refresh()
    upload_page._run()
    finish(app, upload_page)
    assert "network down" in upload_page.output.toPlainText()
    assert len(state.pending_files(state.open_db(prefs.db_path))) == 1


def test_log_page_shows_events(app, window, prefs, tmp_path):
    folder = make_unlabelled_folder(tmp_path, ["a.jpg"])
    scan_page = page(window, "Скан")
    scan_page.folder.setText(str(folder))
    scan_page._run()
    finish(app, scan_page)
    log_page = page(window, "Журнал")
    log_page.refresh()
    assert log_page.table.rowCount() >= 1
    assert log_page.table.item(0, 2).text() == "scan"


def test_task_failure_is_shown_in_the_page(app, window):
    scan_page = page(window, "Скан")

    def boom(conn, progress, should_stop):
        raise ValueError("broken")

    scan_page.start(boom, lambda _result: None)
    finish(app, scan_page)
    assert "ОШИБКА: ValueError: broken" in scan_page.output.toPlainText()


def test_cancelling_stops_a_running_task(app, window):
    scan_page = page(window, "Скан")
    seen = []

    def wait_for_cancel(conn, progress, should_stop):
        while not should_stop():
            pass
        seen.append("stopped")
        return "done"

    scan_page.start(wait_for_cancel, lambda _result: None)
    scan_page._cancel()
    finish(app, scan_page)
    assert seen == ["stopped"]
    assert isinstance(Path(scan_page.ctx.db_path), Path)


def test_upload_without_sftp_login_explains_what_to_do_and_starts_nothing(app, window, prefs, tmp_path):
    folder = make_unlabelled_folder(tmp_path, ["a.jpg"])
    conn = state.open_db(prefs.db_path)
    services.scan(conn, folder.resolve(), services.find_images(folder), None, 1000)
    state.set_metadata(conn, state.files_needing_metadata(conn, 5)[0], "T", ("k",))
    state.assign_batches(conn, 1000)
    conn.close()

    upload_page = page(window, "Загрузка")
    upload_page.refresh()
    upload_page._run()  # no SFTP user saved in the preferences
    assert upload_page._task is None
    text = upload_page.output.toPlainText()
    assert "Настройки" in text and ".env" not in text
