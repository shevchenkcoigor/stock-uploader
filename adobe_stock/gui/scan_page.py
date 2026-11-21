"""Step 1: choose a folder, register its images, see what was found and what is wrong with it."""

import json
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from adobe_stock import services, state
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.file_actions import FILE_PATH_ROLE, attach_file_menu, open_on_double_click
from adobe_stock.gui.header_sort import HeaderSort
from adobe_stock.gui.pager import Pager, pages_in
from adobe_stock.gui.status import drop_cell_widgets, status_cell, view_of
from adobe_stock.gui.task_page import TaskPage
from adobe_stock.gui.thumbnails import (
    DEFAULT_THUMB_SIZE,
    ROW_PADDING,
    THUMB_SIZE_KEY,
    THUMB_SIZES,
    thumb_size_key,
    tile_icon,
)
from adobe_stock.gui.widgets import mono_font

LAST_SCAN_KEY = "last_scan"
RECURSIVE_KEY = "scan_recursive"
PROBLEM_STATUSES = (state.INVALID, state.REVIEW)
PROBLEM_COLUMNS = ("Файл", "Причина", "Статус")
COL_FILE, COL_REASON, COL_STATUS = range(3)
SORT_FIELDS = ("file", "reason", "status")
STATUS_COLUMN_WIDTH = 140
PROBLEMS_PAGE_SIZE = 10
PERCENT = 100
STAT_TILES = (
    ("found", "Найдено"),
    ("new", "Новых"),
    ("known", "Уже в базе"),
    ("problems", "С проблемами"),
)


MB_LIMIT_REASON = re.compile(r"limit is (\d+) MB")
MEGAPIXELS_REASON = re.compile(r"([\d.]+) MP is outside (\d+)-(\d+) MP")


def humanize_reason(reason: str) -> str:
    """Short Russian wording for the validation messages; anything unknown (e.g. Claude's) stays as is."""
    parts = []
    for part in reason.split("; "):
        if part.startswith("cannot read image"):
            parts.append("Файл не открывается")
        elif found := MEGAPIXELS_REASON.search(part):
            megapixels, low, high = float(found[1]), found[2], found[3]
            shown = f"{megapixels:g}".replace(".", ",")
            if megapixels < float(low):
                parts.append(f"Меньше минимального размера ({shown} МП, нужно от {low})")
            else:
                parts.append(f"Больше максимального размера ({shown} МП, не больше {high})")
        elif part.startswith("same content as "):
            parts.append(f"Дубликат файла {Path(part.removeprefix('same content as ')).name}")
        elif found := MB_LIMIT_REASON.search(part):
            parts.append(f"Файл больше {found[1]} МБ")
        elif "color profile" in part:
            parts.append("Цветовой профиль не sRGB")
        elif "is not .jpg" in part or part.startswith("content is "):
            parts.append("Не JPEG")
        else:
            parts.append(part)
    return "; ".join(parts)


def format_count(number: int) -> str:
    """1240 -> '1 240' (no-break space)."""
    return f"{number:,}".replace(",", " ")


def last_scan_text(saved: dict, now: datetime) -> str:
    """'Последнее сканирование: сегодня, 22:40 · найдено 1 240' for the status bar ('' before any scan)."""
    if not saved:
        return ""
    at = datetime.fromisoformat(saved["at"])
    day = "сегодня" if at.date() == now.date() else at.strftime("%d.%m.%Y")
    return f"Последнее сканирование: {day}, {at:%H:%M} · найдено {format_count(saved['found'])}"


def short_path(path: str, folder: str) -> str:
    """Path relative to the scanned folder's parent (trip_2026/day_03/a.jpg); as is when outside it."""
    try:
        return str(Path(path).relative_to(Path(folder).parent))
    except ValueError:
        return path


def panel_with_heading(title: str) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    panel = QFrame()
    panel.setObjectName("panel")
    column = QVBoxLayout(panel)
    column.setContentsMargins(16, 14, 16, 16)
    column.setSpacing(12)
    head = QHBoxLayout()
    heading = QLabel(title)
    heading.setProperty("role", "heading")
    head.addWidget(heading)
    head.addStretch(1)
    column.addLayout(head)
    return panel, column, head


class ScanPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "")
        self._problems: list[tuple[str, str, str]] = []  # (path, reason, status)
        self._folder = ""
        self._page = 0
        for hidden in (self.output, self.progress, self.status, self.cancel_button):
            hidden.setVisible(False)  # this page draws its own progress and messages

        self.form.setSpacing(16)
        self.form.addWidget(self._build_folder_panel())
        self.form.addWidget(self._build_progress_panel())
        self.form.addWidget(self._build_problems_panel(), 1)
        self._show_counts(self._saved_scan().get("counts", {}))

    # ---- construction -------------------------------------------------------------------------

    def _build_folder_panel(self) -> QFrame:
        panel, column, _ = panel_with_heading("Папка с изображениями")
        row = QHBoxLayout()
        self.folder = QLineEdit(self.ctx.prefs.text("image_folder"))
        self.folder.setProperty("mono", True)
        self.folder.setPlaceholderText("/путь/к/папке с JPEG")
        browse = QPushButton("Выбрать папку…")
        browse.clicked.connect(self._browse)
        self.run_button = self.add_action(QPushButton("Сканировать"))
        self.run_button.clicked.connect(self._run)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        row.addWidget(self.run_button)
        column.addLayout(row)
        self.recursive = QCheckBox("Включая вложенные папки")
        self.recursive.setChecked(self.ctx.prefs.text(RECURSIVE_KEY, "1") == "1")
        column.addWidget(self.recursive)
        self.message = QLabel("")
        self.message.setProperty("role", "error")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        column.addWidget(self.message)
        return panel

    def _build_progress_panel(self) -> QFrame:
        panel, column, head = panel_with_heading("Сканирование")
        self.progress_text = QLabel("")
        self.progress_text.setProperty("role", "muted")
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._cancel)
        head.addWidget(self.progress_text)
        head.addWidget(self.stop_button)
        self.bar = QProgressBar()
        self.bar.setRange(0, PERCENT)
        self.bar.setTextVisible(False)
        column.addWidget(self.bar)
        self.current_file = QLabel("")
        self.current_file.setProperty("role", "path")
        column.addWidget(self.current_file)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.counters: dict[str, QLabel] = {}
        for key, title in STAT_TILES:
            tile = QFrame()
            tile.setObjectName("tile")
            inner = QVBoxLayout(tile)
            inner.setContentsMargins(12, 10, 12, 10)
            inner.setSpacing(0)
            caption = QLabel(title)
            caption.setProperty("role", "muted")
            value = QLabel("—")
            value.setProperty("role", "stat-danger" if key == "problems" else "stat")
            inner.addWidget(caption)
            inner.addWidget(value)
            self.counters[key] = value
            tiles.addWidget(tile, 1)
        column.addLayout(tiles)
        return panel

    def _build_problems_panel(self) -> QFrame:
        panel, column, head = panel_with_heading("Проблемные файлы")
        self.to_description = QPushButton("К описанию →")
        self.to_description.setProperty("primary", True)
        head.addWidget(self.to_description)
        self.table = QTableWidget(0, len(PROBLEM_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(PROBLEM_COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_REASON, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_STATUS, STATUS_COLUMN_WIDTH)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.sorter = HeaderSort(
            self.table, PROBLEM_COLUMNS, SORT_FIELDS, field="status", descending=False, on_change=self._sorted
        )
        attach_file_menu(self.table, self._path_at)
        open_on_double_click(self.table)
        column.addWidget(self.table, 1)
        self.pager = Pager()
        self.pager.page_selected.connect(self._go_page)
        column.addWidget(self.pager)
        return panel

    # ---- state --------------------------------------------------------------------------------

    def _saved_scan(self) -> dict:
        try:
            return json.loads(self.ctx.prefs.text(LAST_SCAN_KEY, "{}"))
        except ValueError:
            return {}

    def write(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def refresh(self) -> None:
        try:
            rows = self.ctx.read(state.all_files)
        except services.StateDbError as error:
            self.write(str(error))
            return
        self._problems = [
            (row["path"], row["error"] or "", row["status"])
            for row in rows
            if row["status"] in PROBLEM_STATUSES
        ]
        self._folder = self.folder.text().strip()
        self._fill_problems()

    def _sorted(self) -> None:
        self._page = 0  # a new order starts from the first page
        self._fill_problems()

    def _go_page(self, number: int) -> None:
        self._page = number
        self._fill_problems()

    def _path_at(self, position) -> str:
        item = self.table.itemAt(position)
        return self._row_path(item.row()) if item is not None else ""

    def _row_path(self, row: int) -> str:
        item = self.table.item(row, COL_FILE)
        return item.data(FILE_PATH_ROLE) or "" if item is not None else ""

    def _fit_thumb_size(self) -> int:
        """Row picture side from the S/M/L choice made on the description page."""
        side = THUMB_SIZES[thumb_size_key(self.ctx.prefs.text(THUMB_SIZE_KEY, DEFAULT_THUMB_SIZE))][0]
        self.table.setIconSize(QSize(side, side))
        self.table.verticalHeader().setDefaultSectionSize(side + ROW_PADDING)
        return side

    def _fill_problems(self) -> None:
        side = self._fit_thumb_size()
        keys = {
            "file": lambda item: item[0].lower(),
            "reason": lambda item: humanize_reason(item[1]).lower(),
            "status": lambda item: (view_of(item[2]).rank, item[0].lower()),
        }
        ordered = sorted(self._problems, key=keys[self.sorter.field], reverse=self.sorter.descending)
        self._page = min(self._page, pages_in(len(ordered), PROBLEMS_PAGE_SIZE) - 1)
        shown = ordered[self._page * PROBLEMS_PAGE_SIZE : (self._page + 1) * PROBLEMS_PAGE_SIZE]
        self.pager.show_page(len(ordered), PROBLEMS_PAGE_SIZE, self._page)
        drop_cell_widgets(self.table, COL_STATUS)
        self.table.setRowCount(len(shown))
        for index, (path, reason, status) in enumerate(shown):
            file_item = QTableWidgetItem(short_path(path, self._folder))
            file_item.setToolTip(path)
            file_item.setData(FILE_PATH_ROLE, path)
            file_item.setFont(mono_font())
            file_item.setIcon(tile_icon(path, side, "warning" if status == state.REVIEW else ""))
            self.table.setItem(index, COL_FILE, file_item)
            reason_item = QTableWidgetItem(humanize_reason(reason))
            reason_item.setToolTip(reason)
            self.table.setItem(index, COL_REASON, reason_item)
            self.table.setCellWidget(index, COL_STATUS, status_cell(status))
        self.to_description.setEnabled(True)

    def _cancel(self) -> None:
        super()._cancel()
        self.stop_button.setEnabled(False)

    def _show_counts(self, counts: dict) -> None:
        for key, label in self.counters.items():
            value = counts.get(key)
            label.setText("—" if value is None else format_count(int(value)))

    # ---- actions ------------------------------------------------------------------------------

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Папка с изображениями", self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    def _set_running(self, running: bool) -> None:
        super()._set_running(running)
        self.progress.setVisible(False)  # the base class's bar and label are hidden on this page
        self.stop_button.setEnabled(running)
        if running:
            self.bar.setRange(0, 0)  # busy until the first real progress arrives
        else:
            self.bar.setRange(0, PERCENT)

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            percent = done * PERCENT // total
            self.bar.setRange(0, PERCENT)
            self.bar.setValue(percent)
            self.progress_text.setText(f"{format_count(done)} из {format_count(total)} · {percent}%")
            self.current_file.setText(short_path(message, self._folder))
        else:
            self.progress_text.setText(message)

    def _run(self) -> None:
        root = Path(self.folder.text().strip()).expanduser()
        if not root.is_dir():
            self.write(f"Папка не найдена: {root}")
            return
        self.write("")
        recursive = self.recursive.isChecked()
        self.ctx.prefs.set_text("image_folder", str(root))
        self.ctx.prefs.set_text(RECURSIVE_KEY, "1" if recursive else "0")
        size = self.ctx.prefs.batch_size
        resolved = root.resolve()
        self._folder = str(resolved)

        def work(conn, progress, should_stop):
            progress(0, 0, "Ищу JPEG…")
            images = services.find_images(resolved, recursive)
            if not images:
                return None
            return services.scan(conn, resolved, images, None, size, progress, should_stop)

        self.start(work, self._done)

    def _done(self, report: object) -> None:
        self.current_file.setText("")
        if not isinstance(report, services.ScanReport):
            self.write("JPEG-файлы не найдены.")
            self.progress_text.setText("Нет файлов")
            return
        r = report.result
        counts = {
            "found": report.found,
            "new": r.added + r.needs_metadata,
            "known": r.unchanged,
            "problems": r.invalid,
        }
        self._show_counts(counts)
        done_text = "Остановлено" if report.stopped else "Готово"
        self.progress_text.setText(f"{done_text} · {format_count(report.found)} файлов")
        if not report.stopped:
            self.bar.setValue(PERCENT)
        saved = {"at": datetime.now().isoformat(timespec="seconds"), "found": report.found, "counts": counts}
        self.ctx.prefs.set_text(LAST_SCAN_KEY, json.dumps(saved))
        for name in r.changed_after_upload:
            self.write(f"ВНИМАНИЕ {name} изменён после загрузки, повторно не грузится")
        self.refresh()
