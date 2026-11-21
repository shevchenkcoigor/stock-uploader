import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidgetSelectionRange  # noqa: E402

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
    assert "famous logo" in description.output.toPlainText()

    description.refresh()
    # the captioned file got a batch at once and the flagged one is not for describing: nothing is listed
    assert description.table.rowCount() == 0
    description._approve(None)
    finish(app, description)
    description.refresh()
    assert description.table.rowCount() == 0
    conn = state.open_db(prefs.db_path)
    assert {row["status"] for row in conn.execute("SELECT status FROM files")} == {"pending"}
    conn.close()

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
    assert "несохранённые" in description.dirty_label.text()
    description._save_panel()
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
    description.sorter.click(4)  # size
    assert (
        header_labels(description.table)[4] == "Размер ↑" and header_labels(description.table)[0] == "Имя ↕"
    )


def cell_status(table, row: int) -> str:
    """The status word of a row (the chip shows it; the item keeps it readable)."""
    return table.item(row, 5).data(Qt.ItemDataRole.AccessibleTextRole)


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


def test_only_new_or_described_files_outside_any_batch_are_listed_for_describing(tmp_path):
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
    ]

    assert state.update_metadata(conn, "new.jpg", "T", ("a",), "") is True
    for name in ("held.jpg", "planned.jpg", "sending.jpg", "sent.jpg", "bad.jpg"):  # never described again
        assert state.update_metadata(conn, name, "Other", ("b",), "") is False
    assert conn.execute("SELECT title FROM files WHERE filename = 'sent.jpg'").fetchone()[0] == ""


def test_saving_a_file_that_is_already_in_a_batch_is_refused_with_a_message(app, window, prefs):
    seed_files(prefs, ["a.jpg"])
    window.nav.setCurrentRow(1)  # the description page is the visible one, so it refreshes with the data
    description = page(window, "Описание")
    description.refresh()
    description.table.selectRow(0)
    conn = state.open_db(prefs.db_path)
    conn.execute("INSERT INTO batches (id, folder, part, created_at) VALUES (1, 'f', 1, 'now')")
    conn.execute("UPDATE files SET batch_id = 1, status = 'uploaded'")  # it moved on behind the page's back
    conn.commit()
    description.title_edit.setText("Late edit")
    description._save_panel()
    app.processEvents()
    assert "уже в батче" in description.output.toPlainText()
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
    description.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 2, 5), True)
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

    bar.select_all_clicked.emit()  # all 25, across both pages
    assert bar.count_label.text() == "Выбрано 25"
    description._go_page(1)
    assert bar.count_label.text() == "Выбрано 25"
    assert len(description.table.selectionModel().selectedRows()) == 5
    bar.clear_clicked.emit()
    assert bar.count_label.text() == "Выбрано 0" and not bar.isHidden()


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
    description.bulk_bar.select_all_clicked.emit()
    assert [description.table.item(r, 0).checkState() for r in range(3)] == [Qt.CheckState.Checked] * 3
    description.bulk_bar.clear_clicked.emit()
    assert [description.table.item(r, 0).checkState() for r in range(3)] == [Qt.CheckState.Unchecked] * 3
