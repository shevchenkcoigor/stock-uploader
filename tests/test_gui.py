import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QTableWidgetSelectionRange  # noqa: E402

from adobe_stock import caption, services, state  # noqa: E402
from adobe_stock.gui.context import AppContext  # noqa: E402
from adobe_stock.gui.main_window import MainWindow  # noqa: E402
from adobe_stock.gui.prefs import MemoryStore, Preferences  # noqa: E402
from tests.test_caption import make_policy_claude, make_unlabelled_folder  # noqa: E402
from tests.test_state import make_folder  # noqa: E402


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
        "1. Скан",
        "2. Описание",
        "3. Загрузка",
        "4. CSV и отправка",
        "Журнал",
        "Настройки",
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


class KeychainThatMustNotBeRead(MemoryStore):
    """Reading the OS keychain asks the user for the Mac password: only an SFTP login may do it."""

    def get(self) -> str:
        raise AssertionError("the keychain was read")


def test_settings_have_no_save_button_and_apply_a_committed_change(app, window, prefs):
    settings_page = page(window, "Настройки")
    assert not hasattr(settings_page, "save_button")
    assert not settings_page.cancel_button.isVisibleTo(settings_page)

    settings_page.user.setText("artist")
    settings_page.user.editingFinished.emit()  # leaving the field or pressing Enter
    assert prefs.text("sftp_user") == "artist"

    settings_page.batch_size.setValue(123)
    settings_page.batch_size.editingFinished.emit()
    assert prefs.batch_size == 123

    settings_page.theme.setCurrentIndex(1 - settings_page.theme.currentIndex())
    assert prefs.text("theme") == str(settings_page.theme.currentData())


def test_a_change_is_applied_when_the_page_is_left(app, window, prefs):
    window.show()  # a page is hidden only when it was shown
    window.nav_bottom.setCurrentRow(0)  # the settings page
    settings_page = page(window, "Настройки")
    settings_page.host.setText("sftp.example.test")  # typed, the field was not left yet
    window.nav.setCurrentRow(0)
    assert prefs.text("sftp_host") == "sftp.example.test"


def test_saving_settings_does_not_read_the_keychain(app, window, prefs, tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "secrets", KeychainThatMustNotBeRead())
    monkeypatch.setattr(caption, "find_claude", lambda: None)
    settings_page = page(window, "Настройки")
    settings_page.user.setText("artist")

    assert settings_page._save() is True  # no password typed: the stored one is not needed
    settings_page._check_claude()  # the Claude check has nothing to do with the SFTP password


def test_full_flow_through_the_pages(app, window, prefs, tmp_path, monkeypatch):
    folder = make_unlabelled_folder(tmp_path, ["a.jpg", "b.jpg"])
    monkeypatch.setattr(caption, "find_claude", lambda: "claude")
    flags = {1: {"review_reason": "looks like a famous logo", "people_visible": False}}
    monkeypatch.setattr(caption, "run_claude", make_policy_claude(flags))

    scan_page = page(window, "Скан")
    scan_page.folder.setText(str(folder))
    scan_page._run()
    finish(app, scan_page)
    assert scan_page.counters["found"].text() == "2"
    assert scan_page.counters["new"].text() == "2"
    assert "2 файлов" in scan_page.progress_text.text()
    assert "Последнее сканирование" in window.last_scan.text()

    description = page(window, "Описание")
    description._run_caption()
    finish(app, description)
    assert "Подписано: 1 из 2" in description.output.toPlainText()
    assert "famous logo" not in description.output.toPlainText()
    assert "на проверке: 1" in description.status.text()

    description.refresh()
    # the captioned file is "Описан" and the flagged one "Проверить": both stay on the page
    assert description.table.rowCount() == 2
    assert sorted(cell_status(description.table, r) for r in range(2)) == ["Описан", "Проверить"]
    description._approve(None)
    finish(app, description)
    description.refresh()
    assert description.table.rowCount() == 2
    assert {cell_status(description.table, r) for r in range(2)} == {"Описан"}
    conn = state.open_db(prefs.db_path)
    assert {row["status"] for row in conn.execute("SELECT status FROM files")} == {"pending"}
    conn.close()

    conn = state.open_db(prefs.db_path)
    for path, _ in state.pending_files(conn):
        state.mark_uploaded(conn, path)
    csv_page = page(window, "CSV")
    csv_page.refresh()
    assert csv_page.files.rowCount() == 0  # no CSV yet: «+ CSV» offers the uploaded batch
    batch = conn.execute("SELECT id FROM batches").fetchone()["id"]
    csv_page._build(batch)
    finish(app, csv_page)
    assert "CSV собран: 2 строк" in csv_page.status.text()  # the approved file joined the open batch
    assert (tmp_path / "csv_out").exists() and list((tmp_path / "csv_out").glob("*.csv"))
    assert csv_page.files.rowCount() == 1 and csv_page.table.rowCount() == 2
    assert "На SFTP: 2" in window.summary.text()


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


def log_events(window, prefs, *events):
    conn = state.open_db(prefs.db_path)
    for event in events:
        state.log_event(conn, *event)
    page(window, "Журнал").refresh()
    return page(window, "Журнал")


def shown_messages(log_page):
    return [log_page.table.item(row, 3).text() for row in range(log_page.table.rowCount())]


def test_log_page_search_and_filters(window, prefs):
    log_page = log_events(
        window,
        prefs,
        ("scan", "found 3 files"),
        ("scan", "too small", "warning", "icon.png"),
        ("upload", "connection lost", "error", "city.jpg"),
    )
    assert shown_messages(log_page) == ["found 3 files", "icon.png: too small", "city.jpg: connection lost"]

    log_page.search.setText("CITY")
    assert shown_messages(log_page) == ["city.jpg: connection lost"]
    log_page.search.setText("")
    log_page.level.setCurrentIndex(log_page.level.findData("warning"))
    assert shown_messages(log_page) == ["icon.png: too small"]
    log_page.level.setCurrentIndex(0)
    log_page.section.setCurrentIndex(log_page.section.findData("upload"))
    assert shown_messages(log_page) == ["city.jpg: connection lost"]
    assert log_page.count.text().startswith("Показано 1 из 1")


def test_log_page_sorts_by_level_when_the_header_is_clicked(window, prefs):
    log_page = log_events(window, prefs, ("scan", "a"), ("scan", "b", "error"), ("scan", "c", "warning"))

    log_page.sorter.click(1)  # Уровень, ascending: errors first
    assert shown_messages(log_page) == ["b", "c", "a"]
    log_page.sorter.click(0)  # Время, ascending: oldest first
    assert shown_messages(log_page) == ["a", "b", "c"]
    log_page.sorter.click(0)  # and again: newest first
    assert shown_messages(log_page) == ["c", "b", "a"]


def test_log_page_clear_hides_old_events_but_keeps_them_in_the_database(window, prefs):
    log_page = log_events(window, prefs, ("scan", "old"))

    log_page.clear_shown()
    assert shown_messages(log_page) == []
    state.log_event(state.open_db(prefs.db_path), "scan", "new")
    log_page.refresh()
    assert shown_messages(log_page) == ["new"]
    assert len(state.recent_events(state.open_db(prefs.db_path), 10)) == 2


def test_log_page_copies_the_shown_lines(app, window, prefs):
    log_page = log_events(window, prefs, ("scan", "found", "info"), ("upload", "lost", "error", "a.jpg"))

    log_page.copy_shown()
    lines = QApplication.clipboard().text().splitlines()
    assert [line.split("\t")[1:] for line in lines] == [
        ["info", "scan", "found"],
        ["error", "upload", "a.jpg: lost"],
    ]


def test_log_page_follow_polls_only_while_checked(window, prefs):
    log_page = log_events(window, prefs, ("scan", "one"))

    log_page.follow_button.setChecked(True)
    assert log_page._follow.isActive()
    log_page.follow_button.setChecked(False)
    assert not log_page._follow.isActive()


def test_task_failure_is_shown_in_the_page(app, window):
    scan_page = page(window, "Скан")

    def boom(conn, progress, should_stop):
        raise ValueError("broken")

    scan_page.start(boom, lambda _result: None)
    finish(app, scan_page)
    assert "ОШИБКА: ValueError: broken" in scan_page.message.text()


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


def test_theme_follows_choice_and_system():
    from adobe_stock.gui import theme

    assert theme.resolve_theme(theme.THEME_LIGHT, system_is_dark=True) == theme.THEME_LIGHT
    assert theme.resolve_theme(theme.THEME_DARK, system_is_dark=False) == theme.THEME_DARK
    assert theme.resolve_theme(theme.THEME_SYSTEM, system_is_dark=True) == theme.THEME_DARK
    assert theme.resolve_theme(theme.THEME_SYSTEM, system_is_dark=False) == theme.THEME_LIGHT


def test_stylesheet_uses_design_tokens_for_both_themes():
    from adobe_stock.gui import theme

    for name, colors in theme.TOKENS.items():
        sheet = theme.build_stylesheet(name)
        assert colors["accent"] in sheet
        assert "{" in sheet and "{accent" not in sheet


def test_apply_theme_sets_palette(app):
    from PySide6.QtGui import QPalette

    from adobe_stock.gui import theme

    theme.apply_theme(app, theme.THEME_DARK)
    assert app.palette().color(QPalette.ColorRole.Window).name() == theme.TOKENS[theme.THEME_DARK]["window"]
    theme.apply_theme(app, theme.THEME_LIGHT)


def seed_files(prefs, names):
    folder = prefs.db_path.parent / "imgs"
    conn = state.open_db(prefs.db_path)
    for index, name in enumerate(names):
        path = folder / name
        conn.execute(
            "INSERT INTO files (path, filename, sha256, size, status, updated_at, title, keywords) "
            "VALUES (?, ?, 'x', 1048576, ?, ?, '', '[]')",
            (str(path), name, state.NEEDS_METADATA, f"2026-11-2{index % 10}"),
        )
    conn.commit()
    conn.close()


def test_description_parse_keywords_drops_empty_and_repeated_tags():
    from adobe_stock.gui.description_page import parse_keywords

    assert parse_keywords("ель, дорожка,, ель\n лес ") == ("ель", "дорожка", "лес")


def test_description_editing_saves_and_marks_file_described(app, window, prefs):
    seed_files(prefs, ["a.jpg", "b.jpg"])
    description = page(window, "Описание")
    description.refresh()
    assert cell_status(description.table, 0) == "Новый"

    description.table.selectRow(0)
    description.title_edit.setText("Forest path")
    description.keywords_edit.input.setText("forest, path, forest")
    description.keywords_edit.input.returnPressed.emit()
    description.title_edit.textEdited.emit("Forest path")  # the title is saved after a pause in typing
    description._flush_autosave()
    app.processEvents()

    conn = state.open_db(prefs.db_path)
    row = conn.execute("SELECT title, keywords, status FROM files WHERE filename = 'a.jpg'").fetchone()
    assert (row["title"], row["keywords"], row["status"]) == ("Forest path", '["forest", "path"]', "pending")


def test_description_paginates_and_filters(app, window, prefs):
    seed_files(prefs, [f"f{i:03}.jpg" for i in range(45)])
    description = page(window, "Описание")
    description.refresh()
    assert description.table.rowCount() == 20
    assert description.pager.range_label.text() == "1–20 из 45"
    description._go_page(2)
    assert description.pager.range_label.text() == "41–45 из 45"
    description.status_filter.setCurrentIndex(description.status_filter.findData("uploaded"))
    assert description.pager.range_label.text() == "0–0 из 0"


def test_settings_is_last_and_shown_in_the_bottom_list(window):
    assert window.nav_bottom.count() == 1 and window.nav_bottom.item(0).text() == "Настройки"
    window.nav_bottom.setCurrentRow(0)
    assert window.stack.currentWidget() is page(window, "Настройки")
    assert window.nav.currentRow() == -1


def test_scan_page_lists_problem_files_errors_first(app, window, prefs):
    seed_files(prefs, ["ok.jpg", "bad.jpg", "held.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute(
        "UPDATE files SET status = 'review', error = 'looks like a logo' WHERE filename = 'held.jpg'"
    )
    conn.execute("UPDATE files SET status = 'invalid', error = 'not a JPEG' WHERE filename = 'bad.jpg'")
    conn.commit()
    scan_page = page(window, "Скан")
    scan_page.refresh()
    assert scan_page.table.rowCount() == 2
    assert scan_page.table.item(0, 1).text() == "not a JPEG"  # Ошибка before Проверить
    scan_page.sorter.click(1)  # reason, ascending
    assert scan_page.table.item(0, 1).text() == "looks like a logo"


def test_scan_page_reports_missing_folder_and_can_skip_subfolders(app, window, tmp_path):
    scan_page = page(window, "Скан")
    scan_page.folder.setText(str(tmp_path / "nope"))
    scan_page._run()
    assert "Папка не найдена" in scan_page.message.text()

    folder = make_unlabelled_folder(tmp_path, ["top.jpg"])
    nested = folder / "sub"
    nested.mkdir()
    (nested / "deep.jpg").write_bytes((folder / "top.jpg").read_bytes())
    assert len(services.find_images(folder)) == 2
    assert len(services.find_images(folder, recursive=False)) == 1


def test_scan_reports_progress_and_can_be_stopped(tmp_path):
    folder = make_unlabelled_folder(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    conn = state.open_db(tmp_path / "s.db")
    seen: list[tuple[int, int]] = []
    report = services.scan(
        conn,
        folder.resolve(),
        services.find_images(folder),
        None,
        1000,
        lambda done, total, _path: seen.append((done, total)),
        lambda: len(seen) >= 1,
    )
    assert seen == [(0, 3)] and report.stopped and report.found == 3


def test_last_scan_text_and_count_format():
    from datetime import datetime

    from adobe_stock.gui.scan_page import format_count, last_scan_text

    assert format_count(1240) == "1\u00a0240"
    now = datetime(2026, 11, 21, 23, 0)
    saved = {"at": "2026-11-21T22:40:05", "found": 1240}
    assert last_scan_text(saved, now) == "Последнее сканирование: сегодня, 22:40 · найдено 1\u00a0240"
    assert last_scan_text({"at": "2026-11-20T09:05:00", "found": 3}, now).endswith(
        "20.11.2026, 09:05 · найдено 3"
    )
    assert last_scan_text({}, now) == ""


# ------------------------------------------------------------------ the scan, end to end through the page

SCAN_SIDE = (2200, 2000)  # 4.4 MP: valid for Adobe, tiny on disk (flat colour)


def make_scan_tree(root: Path) -> Path:
    """A trip-like tree: 3 valid pictures (2 nested), plus a too-small one, a fake .jpg and a duplicate."""
    from PIL import Image, ImageDraw

    def picture(path: Path, number: int, size=SCAN_SIDE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", size, (40 + number * 30, 90, 160))
        ImageDraw.Draw(image).rectangle((10, 10, 10 + number, 20), fill=(255, 255, 255))
        image.save(path, "JPEG")

    folder = root / "trip"
    picture(folder / "top_0.jpg", 0)
    picture(folder / "day_01" / "a_1.jpg", 1)
    picture(folder / "day_02" / "b_2.jpg", 2)
    picture(folder / "day_01" / "icon_small.jpg", 3, size=(300, 200))
    (folder / "day_02" / "broken.jpg").write_text("not a picture", encoding="utf-8")
    (folder / "day_02" / "zz_copy.jpg").write_bytes((folder / "top_0.jpg").read_bytes())
    return folder


def scan_counters(scan_page) -> dict[str, str]:
    return {key: label.text() for key, label in scan_page.counters.items()}


def test_scan_through_the_page_counts_lists_problems_and_rescans(app, window, tmp_path, monkeypatch):
    folder = make_scan_tree(tmp_path)
    scan_page = page(window, "Скан")
    progress: list[tuple[int, int]] = []
    original = scan_page._on_progress
    monkeypatch.setattr(
        scan_page,
        "_on_progress",
        lambda done, total, message: (progress.append((done, total)), original(done, total, message)),
    )

    scan_page.folder.setText(str(folder))
    scan_page._run()
    finish(app, scan_page)

    assert scan_counters(scan_page) == {"found": "6", "new": "3", "known": "0", "problems": "3"}
    assert (0, 6) in progress and (5, 6) in progress  # one report per file
    assert scan_page.bar.value() == 100 and "Готово · 6 файлов" in scan_page.progress_text.text()
    reasons = sorted(scan_page.table.item(row, 1).text() for row in range(scan_page.table.rowCount()))
    assert len(reasons) == 3
    assert any(reason == "Файл не открывается" for reason in reasons)
    assert any(reason.startswith("Меньше минимального размера (0,1 МП") for reason in reasons)
    assert any(reason.startswith("Дубликат файла ") for reason in reasons)  # whichever copy came second
    assert "Последнее сканирование" in window.last_scan.text() and "найдено 6" in window.last_scan.text()

    # the same folder again: the 3 good files are known now, the 3 bad ones are still bad
    scan_page._run()
    finish(app, scan_page)
    assert scan_counters(scan_page) == {"found": "6", "new": "0", "known": "3", "problems": "3"}

    # a new picture appears: only it is new
    from PIL import Image

    Image.new("RGB", SCAN_SIDE, (5, 5, 5)).save(folder / "day_01" / "new_one.jpg", "JPEG")
    scan_page._run()
    finish(app, scan_page)
    assert scan_counters(scan_page)["new"] == "1" and scan_counters(scan_page)["known"] == "3"


def test_scan_without_subfolders_sees_only_the_top_level(app, window, tmp_path):
    folder = make_scan_tree(tmp_path)
    scan_page = page(window, "Скан")
    scan_page.folder.setText(str(folder))
    scan_page.recursive.setChecked(False)
    scan_page._run()
    finish(app, scan_page)
    assert scan_counters(scan_page) == {"found": "1", "new": "1", "known": "0", "problems": "0"}
    assert scan_page.table.rowCount() == 0


def test_scan_of_an_empty_folder_says_so(app, window, tmp_path):
    scan_page = page(window, "Скан")
    scan_page.folder.setText(str(tmp_path))
    scan_page._run()
    finish(app, scan_page)
    assert "JPEG-файлы не найдены" in scan_page.message.text()
    assert scan_page.progress_text.text() == "Нет файлов"


def test_humanize_reason_translates_validation_messages_and_keeps_others():
    from adobe_stock.gui.scan_page import humanize_reason

    assert humanize_reason("0.5 MP is outside 4-100 MP") == "Меньше минимального размера (0,5 МП, нужно от 4)"
    assert (
        humanize_reason("120.0 MP is outside 4-100 MP")
        == "Больше максимального размера (120 МП, не больше 100)"
    )
    assert humanize_reason("file is 50.0 MB, limit is 45 MB") == "Файл больше 45 МБ"
    assert humanize_reason("color profile is 'AdobeRGB', expected sRGB") == "Цветовой профиль не sRGB"
    assert humanize_reason("0.5 MP is outside 4-100 MP; same content as /x/a.jpg").endswith(
        "Дубликат файла a.jpg"
    )
    assert humanize_reason("Possible real person") == "Possible real person"


def test_problem_files_are_paginated_and_sorting_goes_back_to_the_first_page(app, window, prefs):
    seed_files(prefs, [f"f{i:02}.jpg" for i in range(25)])
    conn = state.open_db(prefs.db_path)
    conn.execute("UPDATE files SET status = 'invalid', error = 'not a JPEG'")
    conn.commit()
    scan_page = page(window, "Скан")
    scan_page.refresh()
    assert scan_page.table.rowCount() == 10 and scan_page.pager.range_label.text() == "1–10 из 25"

    scan_page._go_page(2)
    assert scan_page.table.rowCount() == 5 and scan_page.pager.range_label.text() == "21–25 из 25"
    assert scan_page.table.item(4, 0).text().endswith("f24.jpg")

    scan_page.sorter.click(0)  # a click on a header starts from the first page again
    assert scan_page.pager.range_label.text() == "1–10 из 25"


def test_visible_pages_shortens_long_lists_with_an_ellipsis():
    from adobe_stock.gui.pager import visible_pages

    assert visible_pages(3, 0) == [0, 1, 2]
    assert visible_pages(20, 0) == [0, 1, None, 19]
    assert visible_pages(20, 10) == [0, None, 9, 10, 11, None, 19]
    assert visible_pages(20, 19) == [0, None, 18, 19]


def test_tile_pixmap_is_a_sharp_square_with_glyph_for_unreadable_files_and_a_warning_dot(app, tmp_path):
    from PIL import Image

    from adobe_stock.gui import theme
    from adobe_stock.gui.thumbnails import DEVICE_SCALE, tile_pixmap

    good = tmp_path / "good.jpg"
    Image.new("RGB", (300, 200), (200, 30, 30)).save(good, "JPEG")
    broken = tmp_path / "broken.jpg"
    broken.write_text("not a picture", encoding="utf-8")
    colors = theme.TOKENS[theme.THEME_LIGHT]

    picture = tile_pixmap(str(good), 40, "", theme.THEME_LIGHT)
    assert picture.width() == 40 * DEVICE_SCALE and picture.devicePixelRatio() == DEVICE_SCALE
    centre = picture.toImage().pixelColor(picture.width() // 2, picture.height() // 2)
    assert centre.red() > 150 and centre.green() < 80  # the picture itself, fitted in the middle
    assert picture.toImage().pixelColor(0, 0).alpha() == 0  # rounded corners, not a plain square

    plain = tile_pixmap(str(broken), 40, "", theme.THEME_LIGHT).toImage()
    flagged = tile_pixmap(str(broken), 40, "warning", theme.THEME_LIGHT).toImage()
    dot = (plain.width() - 8 * DEVICE_SCALE, 8 * DEVICE_SCALE)
    assert flagged.pixelColor(*dot).name() == colors["warning"]
    assert plain.pixelColor(*dot).name() != colors["warning"]


def test_problem_rows_and_description_rows_have_preview_tiles(app, window, prefs):
    seed_files(prefs, ["a.jpg", "b.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute("UPDATE files SET status = 'invalid', error = 'not a JPEG' WHERE filename = 'a.jpg'")
    conn.commit()
    scan_page = page(window, "Скан")
    scan_page.refresh()
    assert not scan_page.table.item(0, 0).icon().isNull()
    description = page(window, "Описание")
    description.refresh()
    assert not description.table.item(0, 0).icon().isNull()


def header_labels(table) -> list[str]:
    return [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())]


def test_scan_table_header_sorts_both_ways_and_shows_arrows(app, window, prefs):
    seed_files(prefs, ["b.jpg", "c.jpg", "a.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute("UPDATE files SET status = 'invalid', error = 'not a JPEG'")
    conn.commit()
    scan_page = page(window, "Скан")
    scan_page.refresh()
    assert header_labels(scan_page.table) == ["Файл ↕", "Причина ↕", "Статус ↑"]  # default: status, ascending

    def names() -> list[str]:
        return [scan_page.table.item(row, 0).text().rsplit("/", 1)[-1] for row in range(3)]

    scan_page.sorter.click(0)
    assert names() == ["a.jpg", "b.jpg", "c.jpg"] and header_labels(scan_page.table)[0] == "Файл ↑"
    scan_page.sorter.click(0)  # the same column again: the other direction
    assert names() == ["c.jpg", "b.jpg", "a.jpg"] and header_labels(scan_page.table)[0] == "Файл ↓"
    scan_page.sorter.click(0)
    assert names() == ["a.jpg", "b.jpg", "c.jpg"]  # and back
    scan_page.sorter.click(2)  # another column starts ascending again
    assert header_labels(scan_page.table) == ["Файл ↕", "Причина ↕", "Статус ↑"]


def test_status_sort_puts_errors_first_and_flips_to_review_first(app, window, prefs):
    seed_files(prefs, ["a.jpg", "b.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute("UPDATE files SET status = 'invalid', error = 'x' WHERE filename = 'a.jpg'")
    conn.execute("UPDATE files SET status = 'review', error = 'y' WHERE filename = 'b.jpg'")
    conn.commit()
    scan_page = page(window, "Скан")
    scan_page.refresh()
    first = lambda: scan_page.table.item(0, 0).text().rsplit("/", 1)[-1]  # noqa: E731
    assert first() == "a.jpg"  # Ошибка before Проверить
    scan_page.sorter.click(2)
    assert first() == "b.jpg" and header_labels(scan_page.table)[2] == "Статус ↓"


def test_description_sorts_only_by_the_table_headers(app, window, prefs):
    seed_files(prefs, ["b.jpg", "c.jpg", "a.jpg"])
    description = page(window, "Описание")
    description.refresh()
    assert not hasattr(description, "sort_field")  # no separate sort menu any more
    description.sorter.click(0)
    assert [description.table.item(r, 0).text() for r in range(3)] == ["a.jpg", "b.jpg", "c.jpg"]
    assert header_labels(description.table)[0] == "Имя ↑"
    description.sorter.click(0)
    assert [description.table.item(r, 0).text() for r in range(3)] == ["c.jpg", "b.jpg", "a.jpg"]
    assert header_labels(description.table)[0] == "Имя ↓"
    description.sorter.click(5)  # size
    assert (
        header_labels(description.table)[5] == "Размер ↑" and header_labels(description.table)[0] == "Имя ↕"
    )


def cell_status(table, row: int) -> str:
    """The status word of a row (the chip shows it; the item keeps it readable)."""
    return table.item(row, 6).data(Qt.ItemDataRole.AccessibleTextRole)


def test_tag_editor_adds_removes_renames_and_deduplicates(app):
    from adobe_stock.gui.tag_editor import TagChip, TagEditor

    editor = TagEditor()
    changes: list[tuple[str, ...]] = []
    editor.changed.connect(lambda: changes.append(editor.tags()))
    editor.set_tags(["лес", "тропа"])
    assert editor.tags() == ("лес", "тропа") and "· 2" in editor._title.text()
    assert changes == []  # loading tags is not an edit

    editor.input.setText("туман, лес,  ")  # several at once; the repeat and the empty one are dropped
    editor.input.returnPressed.emit()
    assert editor.tags() == ("лес", "тропа", "туман") and editor.input.text() == ""
    assert changes[-1] == ("лес", "тропа", "туман") and "· 3" in editor._title.text()

    editor._remove("тропа")
    assert editor.tags() == ("лес", "туман")
    editor._rename("туман", "дымка")
    assert editor.tags() == ("лес", "дымка")
    editor._rename("дымка", "")  # emptied: the tag goes away
    assert editor.tags() == ("лес",)
    editor._rename("лес", "лес")  # same name again changes nothing
    assert len(changes) == 4

    chips = editor._chips.findChildren(TagChip)
    assert [chip.tag for chip in chips if not chip.isHidden()] == [
        "лес"
    ]  # removed ones are only hidden until deleted


def test_description_page_leaves_out_files_that_failed_validation(app, window, prefs):
    seed_files(prefs, ["ok.jpg", "bad.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute("UPDATE files SET status = 'invalid', error = 'not a JPEG' WHERE filename = 'bad.jpg'")
    conn.commit()
    description = page(window, "Описание")
    description.refresh()
    assert description.table.rowCount() == 1 and description.table.item(0, 0).text() == "ok.jpg"
    assert description.pager.range_label.text() == "1–1 из 1"
    filter_values = [description.status_filter.itemData(i) for i in range(description.status_filter.count())]
    assert "invalid" not in filter_values
    scan_page = page(window, "Скан")
    scan_page.refresh()
    assert scan_page.table.rowCount() == 1


def test_description_and_scan_tables_share_the_white_surface_background(app, window, prefs):
    from adobe_stock.gui import theme

    theme.apply_theme(app, theme.THEME_LIGHT)
    surface = theme.TOKENS[theme.THEME_LIGHT]["surface"]
    window.resize(1100, 800)
    window.show()
    for index in (0, 1):  # the scan page, then the description page
        window.nav.setCurrentRow(index)
        app.processEvents()
        table = window.pages[index][1].table
        viewport = table.viewport()
        assert (
            viewport.grab().toImage().pixelColor(viewport.width() - 4, viewport.height() - 4).name()
            == surface
        )


def test_new_described_flagged_and_uploaded_files_are_listed_for_describing(tmp_path):
    conn = state.open_db(tmp_path / "d.db")
    conn.execute("INSERT INTO batches (id, folder, part, created_at) VALUES (1, 'f', 1, 'now')")
    rows = (
        ("new.jpg", "needs_metadata", None),
        ("described.jpg", "pending", None),
        ("held.jpg", "review", None),
        ("planned.jpg", "pending", 1),
        ("sending.jpg", "uploaded", 1),
        ("sent.jpg", "submitted", 1),
        ("bad.jpg", "invalid", None),
    )
    for name, status, batch in rows:
        conn.execute(
            "INSERT INTO files (batch_id, path, filename, sha256, size, status, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, 'now')",
            (batch, f"/x/{name}", name, name, status),
        )
    conn.commit()
    assert [row["filename"] for row in state.files_to_describe(conn)] == [
        "new.jpg",
        "described.jpg",
        "held.jpg",
        "planned.jpg",
        "sending.jpg",
    ]  # not the submitted one, not the invalid one

    assert state.update_metadata(conn, "new.jpg", "T", ("a",), "") is True
    for name in ("held.jpg", "planned.jpg", "sending.jpg"):
        assert state.update_metadata(conn, name, "Other", ("b",), "") is True
    for name in ("sent.jpg", "bad.jpg"):  # final or invalid: never edited
        assert state.update_metadata(conn, name, "Other", ("b",), "") is False
    assert conn.execute("SELECT title FROM files WHERE filename = 'sent.jpg'").fetchone()[0] == ""


def test_saving_a_file_that_was_submitted_meanwhile_is_refused_with_a_message(app, window, prefs):
    seed_files(prefs, ["a.jpg"])
    window.nav.setCurrentRow(1)  # the description page is the visible one, so it refreshes with the data
    description = page(window, "Описание")
    description.refresh()
    description.table.selectRow(0)
    conn = state.open_db(prefs.db_path)
    conn.execute("INSERT INTO batches (id, folder, part, created_at) VALUES (1, 'f', 1, 'now')")
    conn.execute("UPDATE files SET batch_id = 1, status = 'submitted'")  # it moved on behind the page's back
    conn.commit()
    description.title_edit.setText("Late edit")
    description.title_edit.textEdited.emit("Late edit")
    description._flush_autosave()
    app.processEvents()
    assert "уже отправлен" in description.output.toPlainText()
    assert conn.execute("SELECT title FROM files").fetchone()[0] == ""
    assert description.table.rowCount() == 0  # and the list caught up


def test_double_click_on_a_scan_row_opens_the_file(app, window, prefs, monkeypatch):
    opened = []
    monkeypatch.setattr("adobe_stock.gui.scan_page.open_file", opened.append)
    seed_files(prefs, ["bad.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute("UPDATE files SET status = 'invalid', error = 'not a JPEG'")
    conn.commit()
    scan_page = page(window, "Скан")
    scan_page.refresh()
    scan_page.table.cellDoubleClicked.emit(0, 1)  # any cell of the row, not only the name
    assert [Path(path).name for path in opened] == ["bad.jpg"]


def test_description_opens_a_file_by_double_click_on_its_name_only(app, window, prefs, monkeypatch):
    opened = []
    monkeypatch.setattr("adobe_stock.gui.description_page.open_file", opened.append)
    seed_files(prefs, ["a.jpg"])
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    description.table.cellDoubleClicked.emit(0, 1)  # the title cell is edited instead
    assert opened == []
    description.table.cellDoubleClicked.emit(0, 0)
    assert [Path(path).name for path in opened] == ["a.jpg"]

    description.tiles_button.click()
    description.tiles.itemDoubleClicked.emit(description.tiles.item(0))
    assert [Path(path).name for path in opened] == ["a.jpg", "a.jpg"]


def test_open_and_reveal_refuse_a_file_that_is_gone(tmp_path, monkeypatch):
    from adobe_stock.gui import file_actions

    calls = []
    monkeypatch.setattr(file_actions.QDesktopServices, "openUrl", lambda url: calls.append(url) or True)
    monkeypatch.setattr(file_actions.subprocess, "Popen", lambda command: calls.append(command))
    assert file_actions.open_file(str(tmp_path / "gone.jpg")) is False
    assert file_actions.reveal_file("") is False
    assert calls == []
    real = tmp_path / "here.jpg"
    real.write_bytes(b"x")
    assert file_actions.open_file(str(real)) is True
    assert file_actions.reveal_file(str(real)) is True
    assert len(calls) == 2


def test_description_shows_category_name_and_status_on_tiles(app, window, prefs):
    seed_files(prefs, ["a.jpg", "b.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute("UPDATE files SET category = '5' WHERE filename = 'a.jpg'")
    conn.commit()
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    from adobe_stock.categories import CATEGORIES

    shown = {description.table.item(r, 0).text(): description.table.item(r, 3).text() for r in range(2)}
    assert shown == {"a.jpg": CATEGORIES[5], "b.jpg": ""}  # the name, never the code
    assert header_labels(description.table)[3].startswith("Категория")
    description.sorter.click(3)
    assert description.table.item(0, 0).text() == "a.jpg"
    description.sorter.click(3)
    assert description.table.item(0, 0).text() == "b.jpg"  # descending: the empty one comes first

    description.tiles_button.click()
    assert description.tiles.item(0).text().endswith("Новый")


def test_bulk_edit_adds_a_keyword_and_replaces_the_category_only_for_editable_files(tmp_path):
    conn = state.open_db(tmp_path / "d.db")
    conn.execute("INSERT INTO batches (id, folder, part, created_at) VALUES (1, 'f', 1, 'now')")
    rows = (
        ("a.jpg", "needs_metadata", None, "", "[]", ""),
        ("b.jpg", "pending", None, "Title", '["fir", "tree"]', "3"),
        ("c.jpg", "pending", None, "Title", '["tree"]', "3"),
        ("sent.jpg", "submitted", 1, "Title", '["x"]', "3"),
    )
    for name, status, batch, title, keywords, category in rows:
        conn.execute(
            "INSERT INTO files (batch_id, path, filename, sha256, size, status, updated_at, title, keywords, "
            "category) VALUES (?, ?, ?, ?, 1, ?, 'now', ?, ?, ?)",
            (batch, f"/x/{name}", name, name, status, title, keywords, category),
        )
    conn.commit()

    names = ["a.jpg", "b.jpg", "c.jpg", "sent.jpg"]
    assert state.bulk_edit(conn, names, tag="tree", category="7") == 3  # the sent one is left alone

    def stored(name):
        row = conn.execute(
            "SELECT keywords, category, status FROM files WHERE filename = ?", (name,)
        ).fetchone()
        return json.loads(row["keywords"]), row["category"], row["status"]

    assert stored("a.jpg") == (["tree"], "7", "needs_metadata")  # no title yet, so still new
    assert stored("b.jpg") == (["fir", "tree"], "7", "pending")  # never twice
    assert stored("c.jpg") == (["tree"], "7", "pending")
    assert stored("sent.jpg") == (["x"], "3", "submitted")

    assert state.bulk_edit(conn, names, tag="", category=None) == 0  # nothing to apply
    assert state.bulk_edit(conn, ["c.jpg"], tag="new") == 1  # a keyword alone keeps the category
    assert stored("c.jpg") == (["tree", "new"], "7", "pending")


def insert_file(conn, name, status, title="", keywords="[]", error=None):
    conn.execute(
        "INSERT INTO files (path, filename, sha256, size, status, updated_at, title, keywords, error) "
        "VALUES (?, ?, ?, 1, ?, 'now', ?, ?, ?)",
        (f"/x/{name}", name, name, status, title, keywords, error),
    )


def status_of(conn, name):
    return conn.execute("SELECT status, error FROM files WHERE filename = ?", (name,)).fetchone()


def test_set_status_moves_files_by_hand_and_needs_a_description_to_leave_new(tmp_path):
    conn = state.open_db(tmp_path / "d.db")
    insert_file(conn, "new_text.jpg", "needs_metadata", "Title", '["k"]')
    insert_file(conn, "new_empty.jpg", "needs_metadata")
    insert_file(conn, "described.jpg", "pending", "Title", '["k"]')
    insert_file(conn, "flagged.jpg", "review", "Title", '["k"]', "Possible brand")
    insert_file(conn, "sent.jpg", "submitted", "Title", '["k"]')
    insert_file(conn, "bad.jpg", "invalid")
    conn.commit()
    names = ["new_text.jpg", "new_empty.jpg", "described.jpg", "flagged.jpg", "sent.jpg", "bad.jpg"]

    assert state.set_status(conn, names, state.PENDING) == 2  # new_text and flagged; described already is
    assert status_of(conn, "new_text.jpg")["status"] == "pending"
    assert status_of(conn, "new_empty.jpg")["status"] == "needs_metadata"  # nothing to describe it with
    assert status_of(conn, "flagged.jpg")["error"] is None  # the review reason goes away
    assert status_of(conn, "sent.jpg")["status"] == "submitted"  # final ones are never moved
    assert status_of(conn, "bad.jpg")["status"] == "invalid"

    assert state.set_status(conn, ["described.jpg"], state.REVIEW) == 1
    assert status_of(conn, "described.jpg")["error"]  # a reason to show in the card
    assert state.set_status(conn, ["described.jpg", "new_empty.jpg"], state.NEEDS_METADATA) == 1
    row = conn.execute("SELECT title, status FROM files WHERE filename = 'described.jpg'").fetchone()
    assert (row["title"], row["status"]) == ("Title", "needs_metadata")  # the text stays for re-captioning

    with pytest.raises(ValueError):
        state.set_status(conn, ["new_text.jpg"], state.SUBMITTED)  # not a status of the description page


def test_bulk_edit_stops_at_the_keyword_limit(tmp_path):
    conn = state.open_db(tmp_path / "d.db")
    full = json.dumps([f"k{n}" for n in range(49)])
    conn.execute(
        "INSERT INTO files (path, filename, sha256, size, status, updated_at, title, keywords) "
        "VALUES ('/x/a.jpg', 'a.jpg', 'a', 1, 'pending', 'now', 'T', ?)",
        (full,),
    )
    conn.commit()
    state.bulk_edit(conn, ["a.jpg"], tag="one more")
    assert len(json.loads(conn.execute("SELECT keywords FROM files").fetchone()[0])) == 49


def test_description_bulk_bar_edits_all_selected_files(app, window, prefs):
    seed_files(prefs, [f"f{n:02d}.jpg" for n in range(25)])
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    assert not description.bulk_bar.isHidden() and description.bulk_bar.count_label.text() == "Выбрано 0"
    description.table.selectRow(0)
    assert not description.bulk_bar.apply_button.isEnabled()  # one file is edited in the side panel
    description.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 2, 6), True)
    assert description.bulk_bar.apply_button.isEnabled()
    assert description.bulk_bar.count_label.text() == "Выбрано 3"

    bar = description.bulk_bar
    bar.tag_input.setText("forest")
    bar.category_box.setCurrentIndex(bar.category_box.findData("5"))
    bar.apply_button.click()
    app.processEvents()
    conn = state.open_db(prefs.db_path)
    rows = conn.execute("SELECT keywords, category FROM files ORDER BY id").fetchall()
    assert [(json.loads(r[0]), r[1]) for r in rows[:3]] == [(["forest"], "5")] * 3
    assert [(json.loads(r[0]), r[1]) for r in rows[3:]] == [([], "")] * 22
    assert bar.tag_input.text() == ""

    description._toggle_all()  # all 25, across both pages
    assert bar.count_label.text() == "Выбрано 25"
    description._go_page(1)
    assert bar.count_label.text() == "Выбрано 25"
    assert len(description.table.selectionModel().selectedRows()) == 5
    description._toggle_all()  # all are marked, so this clears
    assert bar.count_label.text() == "Выбрано 0" and not bar.isHidden()


def describe_by_sql(prefs, names):
    conn = state.open_db(prefs.db_path)
    for name in names:
        conn.execute(
            "UPDATE files SET title = 'T', keywords = '[\"k\"]', status = 'pending' WHERE filename = ?",
            (name,),
        )
    conn.commit()
    conn.close()


def test_status_is_changed_next_to_the_category_in_the_bulk_bar_and_the_card(app, window, prefs):
    seed_files(prefs, ["a.jpg", "b.jpg", "c.jpg"])
    describe_by_sql(prefs, ["a.jpg", "b.jpg"])  # c.jpg stays new, without a description
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    bar = description.bulk_bar
    assert bar.status_box.itemText(0) == "Статус"

    description.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 2, 6), True)
    bar.status_box.setCurrentIndex(bar.status_box.findData(state.REVIEW))
    bar.apply_button.click()
    app.processEvents()
    conn = state.open_db(prefs.db_path)
    statuses = {r["filename"]: r["status"] for r in conn.execute("SELECT filename, status FROM files")}
    assert statuses == {"a.jpg": "review", "b.jpg": "review", "c.jpg": "needs_metadata"}
    assert "2 из 3" in description.status.text()  # one was skipped: it has no description
    assert bar.status_box.currentIndex() == 0  # like the category: reset after applying

    description._choose("a.jpg")
    box = description.status_box
    assert box.currentData() == state.REVIEW
    index = box.findData(state.PENDING)
    box.setCurrentIndex(index)
    box.activated.emit(index)
    app.processEvents()
    row = conn.execute("SELECT status FROM files WHERE filename = 'a.jpg'").fetchone()
    assert row["status"] == "pending"


def test_a_refused_status_change_says_why(app, window, prefs):
    seed_files(prefs, ["a.jpg", "b.jpg"])  # new, no title and no keywords
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    description.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 6), True)
    bar = description.bulk_bar
    bar.status_box.setCurrentIndex(bar.status_box.findData(state.PENDING))
    bar.apply_button.click()
    app.processEvents()
    assert "0 из 2" in description.status.text()
    assert "без заголовка или ключевых слов: 2" in description.status.text()


def test_status_is_chosen_from_a_menu_in_the_list_row(app, window, prefs, monkeypatch):
    from adobe_stock.gui.description_page import COL_STATUS

    seed_files(prefs, ["a.jpg", "b.jpg"])
    describe_by_sql(prefs, ["a.jpg"])  # a.jpg described, b.jpg new
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    row = next(r for r in range(2) if description.table.item(r, 0).text() == "a.jpg")
    conn = state.open_db(prefs.db_path)

    def status_of_a():
        return conn.execute("SELECT status FROM files WHERE filename = 'a.jpg'").fetchone()[0]

    menu = description._status_menu(state.PENDING)  # the menu lists the statuses, the current one ticked
    assert [action.text() for action in menu.actions()] == [
        "Новый",
        "Описан",
        "Проверить",
        "На SFTP",
    ]  # as in the filter
    assert [action.isChecked() for action in menu.actions()] == [False, True, False, False]

    monkeypatch.setattr(description, "_pick_status", lambda current: state.REVIEW)
    description._status_cell_clicked(row, COL_STATUS)
    app.processEvents()
    assert status_of_a() == "review"

    description._status_cell_clicked(row, 0)  # other columns open nothing
    monkeypatch.setattr(description, "_pick_status", lambda current: None)  # the menu was closed
    description._status_cell_clicked(row, COL_STATUS)
    assert status_of_a() == "review"


def test_description_checkboxes_mark_files_for_the_bulk_bar(app, window, prefs):
    seed_files(prefs, [f"f{n:02d}.jpg" for n in range(25)])
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    first, second = description.table.item(0, 0), description.table.item(1, 0)
    assert first.flags() & Qt.ItemFlag.ItemIsUserCheckable
    first.setCheckState(Qt.CheckState.Checked)
    assert not description.bulk_bar.apply_button.isEnabled()  # one is not a bulk
    second.setCheckState(Qt.CheckState.Checked)
    assert description.bulk_bar.count_label.text() == "Выбрано 2"
    app.processEvents()

    description._go_page(1)  # the marks outlive the page, and come back with it
    description._go_page(0)
    assert description.table.item(0, 0).checkState() == Qt.CheckState.Checked
    description.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not description.bulk_bar.apply_button.isEnabled()

    description.tiles_button.click()  # the same marks on tiles
    assert description.tiles.item(0).checkState() == Qt.CheckState.Checked
    description.tiles.item(2).setCheckState(Qt.CheckState.Checked)
    assert description.bulk_bar.count_label.text() == "Выбрано 2"


def test_description_clear_and_select_all_update_the_checkboxes(app, window, prefs):
    seed_files(prefs, ["a.jpg", "b.jpg", "c.jpg"])
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    description._toggle_all()
    assert [description.table.item(r, 0).checkState() for r in range(3)] == [Qt.CheckState.Checked] * 3
    description._toggle_all()
    assert [description.table.item(r, 0).checkState() for r in range(3)] == [Qt.CheckState.Unchecked] * 3


def test_header_checkbox_marks_and_unmarks_everything_without_sorting(app, window, prefs):
    from PySide6.QtTest import QTest

    seed_files(prefs, [f"f{n:02d}.jpg" for n in range(25)])
    window.nav.setCurrentRow(1)
    window.show()
    description = page(window, "Описание")
    description.refresh()
    header = description.check_header
    sorted_before = (description.sorter.field, description.sorter.descending)

    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=header.box_rect().center())
    app.processEvents()
    assert description.bulk_bar.count_label.text() == "Выбрано 25"  # all pages, not only this one
    assert header.check_state() == Qt.CheckState.Checked
    assert (
        description.sorter.field,
        description.sorter.descending,
    ) == sorted_before  # the click did not sort

    description.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert header.check_state() == Qt.CheckState.PartiallyChecked

    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=header.box_rect().center())
    app.processEvents()
    assert description.bulk_bar.count_label.text() == "Выбрано 25"  # not all were marked, so it marks all
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=header.box_rect().center())
    app.processEvents()
    assert description.bulk_bar.count_label.text() == "Выбрано 0"
    assert header.check_state() == Qt.CheckState.Unchecked


def scan_unlabelled(app, window, tmp_path, monkeypatch, names):
    monkeypatch.setattr(caption, "find_claude", lambda: "claude")
    monkeypatch.setattr(caption, "run_claude", make_policy_claude({}))
    scan_page = page(window, "Скан")
    scan_page.folder.setText(str(make_unlabelled_folder(tmp_path, names)))
    scan_page._run()
    finish(app, scan_page)
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    return description


def titles_by_file(prefs):
    conn = state.open_db(prefs.db_path)
    return {row["filename"]: row["title"] for row in conn.execute("SELECT filename, title FROM files")}


def test_caption_skips_uploaded_files_even_when_marked(app, window, prefs, tmp_path, monkeypatch):
    description = scan_unlabelled(app, window, tmp_path, monkeypatch, ["a.jpg", "b.jpg", "c.jpg"])
    conn = state.open_db(prefs.db_path)
    with conn:
        conn.execute("UPDATE files SET status = ? WHERE filename = 'a.jpg'", (state.UPLOADED,))
    description.refresh()

    description._toggle_all()  # marks all three, one of them uploaded
    assert description.run_button.text() == "✦ Caption (2)"
    description._run_caption()
    finish(app, description)

    titles = titles_by_file(prefs)
    assert not titles["a.jpg"]
    assert titles["b.jpg"] and titles["c.jpg"]


def test_caption_with_only_uploaded_files_marked_describes_nothing(app, window, prefs, tmp_path, monkeypatch):
    description = scan_unlabelled(app, window, tmp_path, monkeypatch, ["a.jpg", "b.jpg"])
    conn = state.open_db(prefs.db_path)
    with conn:
        conn.execute("UPDATE files SET status = ? WHERE filename = 'a.jpg'", (state.UPLOADED,))
    description.refresh()
    row = next(
        r for r in range(description.table.rowCount()) if description.table.item(r, 0).text() == "a.jpg"
    )
    description.table.item(row, 0).setCheckState(Qt.CheckState.Checked)

    description._run_caption()
    finish(app, description)

    assert not any(titles_by_file(prefs).values())  # the marked one is uploaded: nothing else is captioned
    assert "Нечего подписывать" in description.output.toPlainText()


def test_caption_describes_only_the_marked_files_and_everything_when_none_marked(
    app, window, prefs, tmp_path, monkeypatch
):
    description = scan_unlabelled(app, window, tmp_path, monkeypatch, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    names = [description.table.item(r, 0).text() for r in range(4)]
    for row in (1, 3):
        description.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    assert description.run_button.text() == "✦ Caption (2)"

    description._run_caption()
    finish(app, description)
    titles = titles_by_file(prefs)
    assert {name for name, title in titles.items() if title} == {names[1], names[3]}
    assert "Подписано: 2 из 2" in description.output.toPlainText()

    description.refresh()  # the described ones stay listed (and marked); the rest are still new
    description._toggle_all()  # not all were marked, so this marks everything ...
    description._toggle_all()  # ... and this clears the marks
    assert description.run_button.text() == "✦ Caption"
    description._run_caption()
    finish(app, description)
    assert all(titles_by_file(prefs).values())


def test_picked_described_files_can_be_captioned_again_but_uploaded_ones_never(tmp_path):
    conn = state.open_db(tmp_path / "d.db")
    for name, status in (("n", "needs_metadata"), ("d", "pending"), ("r", "review"), ("u", "uploaded")):
        insert_file(conn, f"{name}.jpg", status, "Old", '["k"]')
    conn.commit()
    names = ["n.jpg", "d.jpg", "r.jpg", "u.jpg"]

    assert [p.name for p in state.files_needing_metadata(conn, 10)] == ["n.jpg"]
    assert [p.name for p in state.files_needing_metadata(conn, 10, names)] == ["n.jpg"]  # not asked to redo
    assert [p.name for p in state.files_needing_metadata(conn, 10, names, redo=True)] == [
        "n.jpg",
        "d.jpg",
        "r.jpg",
    ]

    for name in ("d", "u"):
        state.set_metadata(conn, Path(f"/x/{name}.jpg"), "New", ("fresh",), "1", redo=True)
    title = {r["filename"]: (r["title"], r["status"]) for r in conn.execute("SELECT * FROM files")}
    assert title["d.jpg"] == ("New", "pending")
    assert title["u.jpg"] == ("Old", "uploaded")  # an uploaded file is never overwritten
    state.set_metadata(conn, Path("/x/r.jpg"), "Edited meanwhile", ("fresh",))  # not a redo: only new files
    assert title["r.jpg"][0] == "Old"


def titles_and_statuses(prefs):
    conn = state.open_db(prefs.db_path)
    rows = conn.execute("SELECT filename, title, status FROM files")
    return {r["filename"]: (r["title"], r["status"]) for r in rows}


def mark_by_name(description, name):
    row = next(r for r in range(description.table.rowCount()) if description.table.item(r, 0).text() == name)
    description.table.item(row, 0).setCheckState(Qt.CheckState.Checked)


def test_caption_again_replaces_a_description_only_after_the_person_agrees(
    app, window, prefs, tmp_path, monkeypatch
):
    description = scan_unlabelled(app, window, tmp_path, monkeypatch, ["a.jpg", "b.jpg", "c.jpg"])
    description._run_caption()  # nothing marked: all three are new
    finish(app, description)
    conn = state.open_db(prefs.db_path)
    with conn:
        conn.execute("UPDATE files SET title = 'Mine'")
        conn.execute("UPDATE files SET status = 'uploaded' WHERE filename = 'c.jpg'")
    description.refresh()

    asked = []
    monkeypatch.setattr(description, "_confirm_overwrite", lambda count: asked.append(count) or False)
    description._run_caption()  # nothing marked: described files are left alone and nobody is asked
    finish(app, description)
    assert asked == [] and {title for title, _ in titles_and_statuses(prefs).values()} == {"Mine"}

    mark_by_name(description, "a.jpg")
    description._run_caption()  # marked and described: the person says no
    assert asked == [1] and description._task is None
    assert titles_and_statuses(prefs)["a.jpg"][0] == "Mine"

    monkeypatch.setattr(description, "_confirm_overwrite", lambda count: True)
    description._run_caption()  # now yes
    finish(app, description)
    titles = titles_and_statuses(prefs)
    assert titles["a.jpg"][0] != "Mine" and titles["b.jpg"][0] == "Mine"  # only the picked one

    mark_by_name(description, "c.jpg")  # an uploaded file is not captioned, even picked
    monkeypatch.setattr(description, "_confirm_overwrite", lambda count: pytest.fail("nothing to replace"))
    description._marked = {"c.jpg"}
    description._run_caption()
    finish(app, description)
    assert titles_and_statuses(prefs)["c.jpg"] == ("Mine", "uploaded")


def test_caption_has_a_hint_with_the_statuses_it_touches(app, window, prefs):
    description = page(window, "Описание")
    hint = description.run_button.toolTip()
    for label in ("Новый", "Описан", "Проверить", "На SFTP", "Отправлен в Adobe"):
        assert label in hint
    assert description.caption_info.toolTip() == hint == description.card_caption_button.toolTip()


def test_card_caption_describes_only_the_open_file_with_the_shared_model(
    app, window, prefs, tmp_path, monkeypatch
):
    description = scan_unlabelled(app, window, tmp_path, monkeypatch, ["a.jpg", "b.jpg"])
    assert not description.card_caption_button.isEnabled()  # no file is open
    description.table.selectRow(1)
    target = description.table.item(1, 0).text()
    assert description.card_caption_button.isEnabled()

    description.card_model.setCurrentIndex(1)  # the two pickers stay together
    assert description.model.currentIndex() == 1
    description.card_caption_button.click()
    finish(app, description)
    assert prefs.model == description.card_model.currentData()
    assert {name for name, title in titles_by_file(prefs).items() if title} == {target}


def test_files_needing_metadata_can_be_narrowed_to_names(tmp_path):
    conn = state.open_db(tmp_path / "d.db")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        conn.execute(
            "INSERT INTO files (path, filename, sha256, size, status, updated_at) "
            "VALUES (?, ?, ?, 1, 'needs_metadata', 'now')",
            (f"/x/{name}", name, name),
        )
    conn.commit()
    assert [p.name for p in state.files_needing_metadata(conn, 10)] == ["a.jpg", "b.jpg", "c.jpg"]
    assert [p.name for p in state.files_needing_metadata(conn, 10, ["c.jpg", "a.jpg", "x.jpg"])] == [
        "a.jpg",
        "c.jpg",
    ]
    assert state.files_needing_metadata(conn, 10, []) == []


def test_model_titles_are_short_and_readable():
    assert [caption.model_title(model_id) for model_id in caption.MODEL_IDS] == [
        "Haiku 5.5",
        "Sonnet 5.5",
        "Opus 5.5",
        "Fable 5.2",
    ]


def test_card_shows_status_chip_and_resolution_and_caption_sits_above_the_picture(
    app, window, prefs, tmp_path, monkeypatch
):
    description = scan_unlabelled(app, window, tmp_path, monkeypatch, ["a.jpg"])
    assert description.status_chip.isHidden()  # no file is open
    description.table.selectRow(0)
    assert not description.status_chip.isHidden() and "Новый" in description.status_chip.text()
    info = description.info_label.text()
    assert "МБ" in info and " × " in info and info.endswith("px")  # size, then the resolution
    assert description.card_model.itemText(0) == "Haiku 5.5"  # just the name, no "Модель:" in front
    window.show()
    app.processEvents()
    panel = description.preview.parentWidget()
    assert panel is not None
    top_of = lambda widget: widget.mapTo(panel, widget.rect().topLeft()).y()  # noqa: E731
    assert top_of(description.card_caption_button) < top_of(description.preview)


def test_thumbnail_size_buttons_resize_rows_and_tiles_and_are_remembered(app, window, prefs):
    from adobe_stock.gui.description_page import THUMB_SIZES

    seed_files(prefs, ["a.jpg", "b.jpg"])
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    assert description.size_buttons["M"].isChecked()  # medium unless chosen otherwise

    for key, (row_side, tile_side) in THUMB_SIZES.items():
        description.size_buttons[key].click()
        assert [button.isChecked() for button in description.size_buttons.values()] == [
            name == key for name in THUMB_SIZES
        ]
        assert description.table.iconSize().width() == row_side
        assert description.table.verticalHeader().defaultSectionSize() > row_side
        assert description.tiles.iconSize().width() == tile_side
        assert description.table.item(0, 0).icon().availableSizes()[0].width() >= row_side
        assert prefs.text("thumb_size") == key

    from adobe_stock.gui.context import AppContext

    reopened = type(description)(AppContext(prefs))
    assert reopened.size_buttons["L"].isChecked()  # the last choice comes back


def test_category_is_edited_in_the_list_row_and_saved_by_code(app, window, prefs):
    from PySide6.QtWidgets import QComboBox

    from adobe_stock.categories import CATEGORIES

    seed_files(prefs, ["a.jpg", "b.jpg"])
    window.nav.setCurrentRow(1)
    window.show()
    description = page(window, "Описание")
    description.refresh()
    cell = description.table.model().index(0, 3)
    description.table.edit(cell)  # what a double-click does
    editor = description.table.findChild(QComboBox, options=Qt.FindChildOption.FindChildrenRecursively)
    assert editor is not None and editor.itemText(0) == "—"
    choice = editor.findText(CATEGORIES[5])
    editor.setCurrentIndex(choice)
    editor.activated.emit(choice)  # picking an entry commits the edit
    app.processEvents()
    conn = state.open_db(prefs.db_path)
    stored = conn.execute("SELECT category FROM files ORDER BY id").fetchall()
    assert [row[0] for row in stored] == ["5", ""]  # the code is stored, never the name
    assert description.table.item(0, 3).text() == CATEGORIES[5]

    from adobe_stock.gui.category_delegate import category_code

    assert category_code(CATEGORIES[5]) == "5" and category_code("") == "" and category_code("nope") == ""


def test_resolution_column_shows_pixels_and_sorts_by_them(app, window, prefs, tmp_path):
    from PIL import Image

    folder = prefs.db_path.parent / "imgs"
    folder.mkdir(parents=True, exist_ok=True)
    sizes = {"small.jpg": (40, 30), "big.jpg": (400, 300), "mid.jpg": (200, 100)}
    for name, side in sizes.items():
        Image.new("RGB", side).save(folder / name, "JPEG")
    seed_files(prefs, list(sizes))
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    shown = {description.table.item(r, 0).text(): description.table.item(r, 4).text() for r in range(3)}
    assert shown == {"small.jpg": "40 × 30", "big.jpg": "400 × 300", "mid.jpg": "200 × 100"}
    assert header_labels(description.table)[4].startswith("Разрешение")

    description.sorter.click(4)
    assert [description.table.item(r, 0).text() for r in range(3)] == ["small.jpg", "mid.jpg", "big.jpg"]
    description.sorter.click(4)
    assert [description.table.item(r, 0).text() for r in range(3)] == ["big.jpg", "mid.jpg", "small.jpg"]


def test_long_file_names_and_keywords_do_not_widen_the_window(app, window, prefs):
    from PySide6.QtWidgets import QWidget

    from adobe_stock.gui.widgets import ElidedLabel

    long_name = "a_really_long_file_name_without_any_spaces_or_breaks_" * 4 + ".jpg"
    long_tag = "averyveryverylongkeywordwithoutspaces" * 3
    conn = state.open_db(prefs.db_path)
    conn.execute(
        "INSERT INTO files (path, filename, sha256, size, status, updated_at, title, keywords) "
        "VALUES (?, ?, 'x', 1, 'pending', 'now', 'T', ?)",
        (f"/x/{long_name}", long_name, json.dumps([long_tag, "short"])),
    )
    conn.commit()
    window.nav.setCurrentRow(1)
    window.show()
    app.processEvents()
    description = page(window, "Описание")
    description.refresh()
    before = window.minimumSizeHint().width()
    description.table.selectRow(0)
    app.processEvents()

    panel = description.preview.parentWidget()
    assert panel is not None
    too_wide = [
        type(child).__name__
        for child in panel.findChildren(QWidget)
        if not child.isHidden() and child.minimumSizeHint().width() > panel.width()
    ]
    assert too_wide == []  # nothing in the card insists on being wider than the card
    assert window.minimumSizeHint().width() == before

    assert description.name_label.text() == long_name  # the full name is kept (and in the tooltip) ...
    assert isinstance(description.name_label, ElidedLabel)
    assert "…" in QLabel.text(description.name_label)  # ... while the shown text is cut


def test_elided_label_keeps_the_full_text_and_never_forces_its_width(app):
    from adobe_stock.gui.widgets import ElidedLabel

    label = ElidedLabel("x" * 300, max_width=120)
    assert label.text() == "x" * 300
    assert label.minimumSizeHint().width() == 0
    assert label.sizeHint().width() <= 120
    label.resize(80, 20)
    assert QLabel.text(label).endswith("x") and "…" in QLabel.text(label)


def test_metadata_card_never_overlaps_its_fields_in_a_short_window(app, window, prefs):
    seed_files(prefs, ["a.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute(
        "UPDATE files SET title = 'T', keywords = ?, status = 'pending'",
        (json.dumps([f"keyword{n}" for n in range(30)]),),
    )
    conn.commit()
    window.resize(1250, 520)  # short, as when the result bar is open at the bottom
    window.show()
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    description.output.setVisible(True)
    description.table.selectRow(0)
    app.processEvents()
    app.processEvents()

    layout = description.card_body.layout()
    assert layout is not None
    previous_bottom = 0
    for index in range(layout.count()):
        item = layout.itemAt(index)
        slot = item.geometry()
        if item.isEmpty() or slot.height() <= 0:  # hidden widgets keep a stale slot
            continue
        assert slot.y() >= previous_bottom, f"item {index} overlaps the one above it"
        previous_bottom = slot.y() + slot.height()


def count_red_pixels(table, row: int, column: int) -> int:
    """How many strongly red pixels the cell paints (the underline of an empty cell)."""
    from PySide6.QtGui import QImage

    image = QImage(table.viewport().size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    table.viewport().render(image)
    rect = table.visualRect(table.model().index(row, column))
    red = 0
    for x in range(rect.left(), rect.right()):
        for y in range(rect.top(), rect.bottom()):
            color = image.pixelColor(x, y)
            if color.red() > 180 and color.green() < 90 and color.blue() < 90:
                red += 1
    return red


def test_empty_title_keywords_and_category_cells_are_underlined_in_red(app, window, prefs):
    seed_files(prefs, ["empty.jpg", "full.jpg"])
    conn = state.open_db(prefs.db_path)
    conn.execute(
        "UPDATE files SET title = 'A title', keywords = '[\"tag\"]', category = '5', status = 'pending' "
        "WHERE filename = 'full.jpg'"
    )
    conn.commit()
    window.resize(1400, 600)
    window.show()
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    app.processEvents()
    rows = {description.table.item(r, 0).text(): r for r in range(2)}

    for column in (1, 2, 3):  # title, keywords, category
        assert count_red_pixels(description.table, rows["empty.jpg"], column) > 20, column
        assert count_red_pixels(description.table, rows["full.jpg"], column) == 0, column
    assert count_red_pixels(description.table, rows["empty.jpg"], 0) == 0  # only the three, not the name


def stored_row(prefs, filename):
    conn = state.open_db(prefs.db_path)
    return conn.execute(
        "SELECT title, keywords, category FROM files WHERE filename = ?", (filename,)
    ).fetchone()


def test_card_saves_by_itself_without_save_and_cancel_buttons(app, window, prefs):
    seed_files(prefs, ["a.jpg", "b.jpg"])
    window.nav.setCurrentRow(1)
    description = page(window, "Описание")
    description.refresh()
    for gone in ("save_button", "revert_button", "dirty_label"):
        assert not hasattr(description, gone)

    description.table.selectRow(0)
    first = description.table.item(0, 0).text()

    description.keywords_edit.input.setText("forest")
    description.keywords_edit.input.returnPressed.emit()  # a tag is saved at once
    assert json.loads(stored_row(prefs, first)["keywords"]) == ["forest"]

    description.category_box.setCurrentIndex(description.category_box.findData("5"))  # so is the category
    assert stored_row(prefs, first)["category"] == "5"

    description.title_edit.setText("Typed title")
    description.title_edit.textEdited.emit("Typed title")
    assert stored_row(prefs, first)["title"] == ""  # the title waits for a pause in typing ...
    description.table.selectRow(1)  # ... or for another file to be opened
    assert stored_row(prefs, first)["title"] == "Typed title"
    assert description.title_edit.text() == ""  # the second file's own (empty) title


def test_title_is_saved_after_a_pause_and_a_refresh_does_not_overwrite_typing(app, window, prefs):
    seed_files(prefs, ["a.jpg"])
    window.nav.setCurrentRow(1)
    window.show()
    description = page(window, "Описание")
    description.refresh()
    description.table.selectRow(0)
    description.title_edit.setFocus()
    description.title_edit.setText("Half typed")
    description.title_edit.textEdited.emit("Half typed")
    description.refresh()  # data arrived meanwhile: the unsaved text stays
    assert description.title_edit.text() == "Half typed"
    description._autosave_timer.timeout.emit()  # the pause is over
    assert stored_row(prefs, "a.jpg")["title"] == "Half typed"


def uploadable_batch(prefs, tmp_path, names):
    folder = make_unlabelled_folder(tmp_path, names)
    services.scan(state.open_db(prefs.db_path), folder.resolve(), services.find_images(folder), None, 1000)
    conn = state.open_db(prefs.db_path)
    for item in state.files_needing_metadata(conn, 10):
        state.set_metadata(conn, item, "T", ("k",))
    state.assign_batches(conn, 1000)


def test_upload_page_lists_the_queue_of_the_batch(app, window, prefs, tmp_path):
    uploadable_batch(prefs, tmp_path, ["a.jpg", "b.jpg"])
    upload_page = page(window, "Загрузка")

    upload_page.refresh()

    assert upload_page.files.rowCount() == 2
    assert upload_page.summary.text() == "0 из 2 загружено"
    assert upload_page.run_button.isEnabled() and not upload_page.retry_button.isEnabled()


def test_a_failed_file_is_shown_as_an_error_and_can_be_retried(app, window, prefs, tmp_path, monkeypatch):
    uploadable_batch(prefs, tmp_path, ["a.jpg", "b.jpg"])

    def one_fails(settings, files, on_success, on_failure, should_stop):
        on_success(files[0][0])
        on_failure(files[1][1], "timed out")
        return [files[1][1]]

    monkeypatch.setattr(services, "upload_files", one_fails)
    prefs.set_text("sftp_user", "me")
    upload_page = page(window, "Загрузка")
    upload_page.refresh()

    upload_page._run()
    finish(app, upload_page)

    assert upload_page.summary.text() == "1 из 2 загружено · 1 ошибка"
    assert upload_page.files.rowCount() == 2  # the queue keeps the uploaded file and the failed one
    assert upload_page.retry_button.isEnabled()
    assert "есть ошибки" in upload_page.status.text()


def uploaded_csv_page(app, window, prefs):
    """A CSV page showing one built CSV of two uploaded files."""
    conn = state.open_db(prefs.db_path)
    folder = make_folder(Path(prefs.db_path).parent, "f1", ["a.jpg", "b.jpg"])
    services.scan(conn, folder.resolve(), services.find_images(folder), None, 1000)
    for path, _ in state.pending_files(conn):
        state.mark_uploaded(conn, path)
    state.update_metadata(conn, "a.jpg", "Sea", ("sea",), "")
    state.update_metadata(conn, "b.jpg", "Sky", ("sky",), "")
    batch = conn.execute("SELECT id FROM batches").fetchone()["id"]
    conn.close()
    csv_page = page(window, "CSV")
    csv_page._build(batch)
    finish(app, csv_page)
    return csv_page, batch


def test_csv_page_edit_is_saved_to_the_database_and_the_csv_file(app, window, prefs):
    csv_page, _ = uploaded_csv_page(app, window, prefs)
    row = next(r for r in range(2) if csv_page.table.item(r, 0).text() == "a.jpg")

    csv_page.table.item(row, 1).setText("Blue sea")

    conn = state.open_db(prefs.db_path)
    assert conn.execute("SELECT title FROM files WHERE filename = 'a.jpg'").fetchone()["title"] == "Blue sea"
    written = next(prefs.csv_dir.glob("*batch*.csv")).read_text(encoding="utf-8")
    assert "a.jpg,Blue sea" in written
    conn.close()


def test_csv_page_row_without_title_turns_the_file_into_an_error(app, window, prefs):
    csv_page, _ = uploaded_csv_page(app, window, prefs)
    row = next(r for r in range(2) if csv_page.table.item(r, 0).text() == "a.jpg")

    csv_page.table.item(row, 1).setText("")
    csv_page.refresh()

    assert "1 с ошибкой" in csv_page.subtitle.text()


def test_csv_page_submitted_rows_cannot_be_edited(app, window, prefs):
    csv_page, batch = uploaded_csv_page(app, window, prefs)
    conn = state.open_db(prefs.db_path)
    services.mark_submitted(conn, batch)
    conn.close()

    csv_page.refresh()

    assert not csv_page.table.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable


def test_csv_page_never_reads_the_keychain(app, window, prefs, monkeypatch):
    monkeypatch.setattr(
        prefs, "secrets", KeychainThatMustNotBeRead()
    )  # reading it makes macOS ask for a password
    csv_page, _ = uploaded_csv_page(app, window, prefs)
    row = next(r for r in range(2) if csv_page.table.item(r, 0).text() == "a.jpg")

    csv_page.table.item(row, 1).setText("Blue sea")  # an edit rewrites the CSV

    assert "Сохранено" in csv_page.status.text()


def test_csv_page_add_is_disabled_with_a_hint_when_there_is_nothing_to_add(app, window, prefs):
    csv_page, _ = uploaded_csv_page(app, window, prefs)  # its only batch already has a CSV
    csv_page.refresh()

    assert not csv_page.add_button.isEnabled()
    assert not csv_page.add_hint.isHidden() and "Нет картинок" in csv_page.add_hint.text()


def test_csv_page_add_is_enabled_for_an_uploaded_batch_without_csv(app, window, prefs):
    conn = state.open_db(prefs.db_path)
    folder = make_folder(Path(prefs.db_path).parent, "f1", ["a.jpg"])
    services.scan(conn, folder.resolve(), services.find_images(folder), None, 1000)
    for path, _ in state.pending_files(conn):
        state.mark_uploaded(conn, path)
    conn.close()
    csv_page = page(window, "CSV")

    csv_page.refresh()

    assert csv_page.add_button.isEnabled() and csv_page.add_hint.isHidden()


def test_long_status_message_does_not_widen_the_window(app, window, prefs):
    csv_page, _ = uploaded_csv_page(app, window, prefs)
    window.show()
    width = window.width()

    csv_page.status.setText("очень длинное сообщение " * 40)
    app.processEvents()

    assert window.width() == width
    assert csv_page.status.minimumSizeHint().width() < width


def test_csv_page_headers_are_adobes_and_releases_is_not_cut(app, window, prefs):
    csv_page, _ = uploaded_csv_page(app, window, prefs)
    window.show()
    header = csv_page.table.horizontalHeader()
    titles = [csv_page.table.horizontalHeaderItem(c).text() for c in range(csv_page.table.columnCount())]

    assert [t.split(" ")[0] for t in titles] == ["Filename", "Title", "Keywords", "Category", "Releases"]
    assert header.sectionSize(4) >= header.fontMetrics().horizontalAdvance("Releases")
    assert header.sectionSize(4) < 150  # still narrow


def test_csv_page_names_the_batch_in_the_list(app, window, prefs):
    csv_page, batch = uploaded_csv_page(app, window, prefs)

    assert csv_page.files.item(0, 1).text() == "f1 · 1"
    assert f"Батч {batch}" in csv_page.files.item(0, 1).toolTip()


def test_csv_page_blocks_sending_a_batch_that_is_not_fully_uploaded(app, window, prefs):
    conn = state.open_db(prefs.db_path)
    folder = make_folder(Path(prefs.db_path).parent, "f1", ["a.jpg", "b.jpg", "c.jpg"])
    services.scan(conn, folder.resolve(), services.find_images(folder), None, 1000)
    state.mark_uploaded(conn, state.pending_files(conn)[0][0])  # one of three is on SFTP
    services.build_csv(conn, 1, prefs.build_settings(with_password=False))
    conn.close()
    csv_page = page(window, "CSV")

    csv_page.refresh()

    assert not csv_page.sent_button.isEnabled()
    assert not csv_page.send_hint.isHidden()
    assert "батч 1 (f1 · 1) загружен не полностью" in csv_page.send_hint.text()
    assert "Описан: 2" in csv_page.send_hint.text()


def test_csv_page_allows_sending_a_fully_uploaded_batch(app, window, prefs):
    csv_page, _ = uploaded_csv_page(app, window, prefs)

    csv_page.refresh()

    assert csv_page.sent_button.isEnabled() and csv_page.send_hint.isHidden()


def rebuild_button(csv_page, row: int):
    return csv_page.files.cellWidget(row, 4).findChild(type(csv_page.add_button))


def test_csv_page_has_no_bottom_buttons_and_a_rebuild_button_per_csv(app, window, prefs):
    csv_page, batch = uploaded_csv_page(app, window, prefs)

    assert not hasattr(csv_page, "build_button") and not hasattr(csv_page, "folder_button")
    button = rebuild_button(csv_page, 0)
    assert button.text() == "Пересобрать" and button.isEnabled()
    next(prefs.csv_dir.glob("*batch*.csv")).unlink()

    button.click()
    finish(app, csv_page)

    assert list(prefs.csv_dir.glob("*batch*.csv"))  # written again


def test_csv_page_sent_csv_has_no_rebuild_button(app, window, prefs):
    csv_page, batch = uploaded_csv_page(app, window, prefs)
    conn = state.open_db(prefs.db_path)
    services.mark_submitted(conn, batch)
    conn.close()

    csv_page.refresh()

    assert csv_page.files.cellWidget(0, 4) is None


def test_csv_page_double_click_on_the_name_opens_the_file(app, window, prefs, monkeypatch):
    csv_page, _ = uploaded_csv_page(app, window, prefs)
    opened: list[str] = []
    monkeypatch.setattr("adobe_stock.gui.csv_page.open_file", lambda path: opened.append(path) or True)

    csv_page._file_double_clicked(0, 0)
    csv_page._file_double_clicked(0, 2)  # another column: nothing happens

    assert len(opened) == 1 and opened[0].endswith(".csv") and Path(opened[0]).exists()


def test_csv_page_says_when_the_csv_file_is_gone(app, window, prefs):
    csv_page, _ = uploaded_csv_page(app, window, prefs)
    next(prefs.csv_dir.glob("*batch*.csv")).unlink()

    csv_page._file_double_clicked(0, 0)

    assert "Файл не найден" in csv_page.status.text()


def test_batch_status_text_is_one_word_or_the_counts():
    from adobe_stock.gui.status import batch_status_text

    assert batch_status_text(((state.UPLOADED, 3),)) == "На SFTP"
    assert batch_status_text(((state.UPLOADED, 1), (state.PENDING, 2))) == "Описан: 2, На SFTP: 1"
    assert batch_status_text(()) == "—"


def test_csv_list_tooltip_shows_the_batch_status(app, window, prefs):
    csv_page, _ = uploaded_csv_page(app, window, prefs)

    assert "Статус батча: На SFTP" in csv_page.files.item(0, 1).toolTip()


def test_upload_batch_list_shows_the_batch_status(app, window, prefs):
    conn = state.open_db(prefs.db_path)
    folder = make_folder(Path(prefs.db_path).parent, "f1", ["a.jpg", "b.jpg", "c.jpg"])
    services.scan(conn, folder.resolve(), services.find_images(folder), None, 1000)
    state.mark_uploaded(conn, state.pending_files(conn)[0][0])  # one of three is on SFTP
    conn.close()
    upload = page(window, "Загрузка")

    upload.refresh()

    assert "статус: Описан: 2, На SFTP: 1" in upload.batch.itemText(0)
