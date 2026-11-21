"""Step 4: the CSV files of the batches (left) and the rows of the chosen one, editable (right)."""

import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from adobe_stock import csv_batches, services, state
from adobe_stock.gui.category_delegate import CategoryDelegate, category_code
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.description_page import category_name
from adobe_stock.gui.empty_cell import EmptyCellDelegate
from adobe_stock.gui.header_sort import HeaderSort
from adobe_stock.gui.pager import Pager
from adobe_stock.gui.status import StatusView, drop_cell_widgets, view_cell
from adobe_stock.gui.tag_editor import parse_keywords
from adobe_stock.gui.task_page import TaskPage
from adobe_stock.gui.widgets import mono_font
from adobe_stock.metadata_csv import HEADERS

FILES_WIDTH = 360
ROW_PAGE_SIZE = 20
ROW_HEIGHT = 40
LIST_COLUMNS = ("Имя", "Строк", "Статус")
LIST_FIELDS = ("name", "rows", "status")
COL_NAME, COL_ROWS, COL_STATE = range(3)
ROW_COLUMNS = HEADERS  # Adobe's own names: Filename, Title, Keywords, Category, Releases
ROW_FIELDS = ("name", "title", "keywords", "category", None)  # Releases cannot be sorted
COL_FILENAME, COL_TITLE, COL_KEYWORDS, COL_CATEGORY, COL_RELEASES = range(5)
HEADER_PADDING = 40  # the header cell's own padding around its text
EDITABLE_COLUMNS = (COL_TITLE, COL_KEYWORDS, COL_CATEGORY)
ENTRY_VIEWS = {
    csv_batches.ERROR: StatusView("Ошибка", "danger", 0),
    csv_batches.BUILT: StatusView("Собран", "accent", 1),
    csv_batches.SENT: StatusView("Отправлен", "success", 2),
}
NOTHING_TO_ADD = (
    "Нет картинок, которые можно добавить в батч: сначала загрузите их по SFTP (страница «Загрузка»)."
)
PORTAL_STEPS = (
    "Дальше в портале Adobe Stock: Uploaded Files → New → Upload CSV; галочки отметьте по чеклисту. "
    "Загрузка CSV и отправка выполняются вручную."
)


def plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def rows_text(entry: csv_batches.CsvEntry) -> str:
    text = f"{entry.rows} {plural(entry.rows, 'строка', 'строки', 'строк')}"
    if entry.bad_rows:
        text += f" · {entry.bad_rows} {plural(entry.bad_rows, 'с ошибкой', 'с ошибками', 'с ошибками')}"
    return text


def row_sort_key(field: str):
    if field == "name":
        return lambda row: row["filename"].lower()
    if field == "title":
        return lambda row: (row["title"] == "", row["title"].lower())  # empty ones go last
    if field == "keywords":
        return lambda row: (row["keywords"] == "[]", row["keywords"].lower())
    return lambda row: (not row["category"], category_name(row["category"]).lower())


def entry_sort_key(field: str):
    if field == "name":
        return lambda entry: entry.name.lower()
    if field == "rows":
        return lambda entry: entry.rows
    return lambda entry: (ENTRY_VIEWS[entry.state].rank, entry.name.lower())


def panel() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    inside = QVBoxLayout(frame)
    return frame, inside


def read_only(item: QTableWidgetItem) -> QTableWidgetItem:
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class CsvPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "")
        self._entries: list[csv_batches.CsvEntry] = []
        self._shown: list[sqlite3.Row] = []  # the rows on the current page of the chosen CSV
        self._rows: list[sqlite3.Row] = []  # all rows of the chosen CSV
        self._batch: int | None = None
        self._page = 0
        self._loading = False
        self._csv_dir: Path | None = None

        self.build_button = self.add_action(QPushButton("Собрать CSV"))  # the first action is the main one
        self.folder_button = self.add_action(QPushButton("Папка"))
        self.add_button = self.add_action(QPushButton("+ CSV"))
        self.sent_button = self.add_action(QPushButton("Отметить отправленным"))
        self.build_button.clicked.connect(lambda: self._build(self._batch))
        self.folder_button.clicked.connect(self._open_folder)
        self.add_button.clicked.connect(self._add)
        self.sent_button.clicked.connect(self._submitted)
        self.cancel_button.setVisible(False)
        self.output.setVisible(False)  # the details are in «Журнал»; the status line says what happened

        self.files = self._make_files_table()
        self.table = self._make_rows_table()
        self._offered: list[sqlite3.Row] = []  # batches «+ CSV» can make a CSV for
        self.add_hint = QLabel(NOTHING_TO_ADD)
        self.add_hint.setProperty("role", "muted")
        self.add_hint.setWordWrap(True)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "muted")
        self.title_label = QLabel("")
        self.title_label.setProperty("role", "heading")
        self.subtitle = QLabel("")
        self.subtitle.setProperty("role", "muted")
        self.pager = Pager()
        self.pager.page_selected.connect(self._go_page)
        self.form.addLayout(self._layout())
        self.list_sorter = HeaderSort(
            self.files,
            LIST_COLUMNS,
            LIST_FIELDS,
            field="status",
            descending=False,
            on_change=self._fill_files,
        )
        self.row_sorter = HeaderSort(
            self.table, ROW_COLUMNS, ROW_FIELDS, field="name", descending=False, on_change=self._fill_rows
        )

    # ------------------------------------------------------------------ layout

    def _layout(self) -> QHBoxLayout:
        left, left_box = panel()
        left.setFixedWidth(FILES_WIDTH)
        heading = QLabel("Файлы")
        heading.setProperty("role", "heading")
        head = QHBoxLayout()
        head.addWidget(heading)
        head.addStretch(1)
        head.addWidget(self.count_label)
        head.addWidget(self.add_button)
        left_box.addLayout(head)
        left_box.addWidget(self.add_hint)
        left_box.addWidget(self.files, 1)
        foot = QHBoxLayout()
        foot.addWidget(self.build_button)
        foot.addWidget(self.folder_button)
        foot.addStretch(1)
        left_box.addLayout(foot)

        right, right_box = panel()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle)
        autosave = QLabel("Сохраняется автоматически")
        autosave.setProperty("role", "muted")
        right_head = QHBoxLayout()
        right_head.addLayout(titles, 1)
        right_head.addWidget(autosave, 0, Qt.AlignmentFlag.AlignTop)
        right_head.addWidget(self.sent_button, 0, Qt.AlignmentFlag.AlignTop)
        right_box.addLayout(right_head)
        right_box.addWidget(self.table, 1)
        right_box.addWidget(self.pager)

        both = QHBoxLayout()
        both.addWidget(left)
        both.addWidget(right, 1)
        return both

    def _make_files_table(self) -> QTableWidget:
        table = QTableWidget(0, len(LIST_COLUMNS))
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setShowGrid(False)
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(COL_ROWS, 60)
        table.setColumnWidth(COL_STATE, 130)
        table.itemSelectionChanged.connect(self._file_selected)
        return table

    def _make_rows_table(self) -> QTableWidget:
        table = QTableWidget(0, len(ROW_COLUMNS))
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        table.setShowGrid(False)
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_KEYWORDS, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(COL_FILENAME, 180)
        table.setColumnWidth(COL_CATEGORY, 150)
        header.setSectionResizeMode(COL_RELEASES, QHeaderView.ResizeMode.Fixed)
        # always empty (AI images have no releases): as narrow as the full word «Releases» allows
        table.setColumnWidth(
            COL_RELEASES, header.fontMetrics().horizontalAdvance(HEADERS[-1]) + HEADER_PADDING
        )
        table.setItemDelegateForColumn(COL_TITLE, EmptyCellDelegate("Нет заголовка", table))
        table.setItemDelegateForColumn(COL_KEYWORDS, EmptyCellDelegate("Нет ключевых слов", table))
        table.setItemDelegateForColumn(COL_CATEGORY, CategoryDelegate("Нет категории", table))
        table.itemChanged.connect(self._cell_edited)
        return table

    # ------------------------------------------------------------------ what is shown

    def refresh(self) -> None:
        self._offered = self.ctx.read(csv_batches.batches_without_csv)
        self._entries = self.ctx.read(csv_batches.csv_entries)
        if self._batch not in {entry.batch_id for entry in self._entries}:
            self._batch = None
        self._fill_files()

    def _chosen(self) -> csv_batches.CsvEntry | None:
        return next((entry for entry in self._entries if entry.batch_id == self._batch), None)

    def _fill_files(self) -> None:
        sort = entry_sort_key(self.list_sorter.field)
        entries = sorted(self._entries, key=sort, reverse=self.list_sorter.descending)
        if self._batch is None and entries:
            self._batch = entries[0].batch_id
        self._loading = True
        self.files.blockSignals(True)
        drop_cell_widgets(self.files, COL_STATE)
        self.files.setRowCount(len(entries))
        for index, entry in enumerate(entries):
            name = read_only(QTableWidgetItem(entry.name))
            name.setFont(mono_font())
            name.setData(Qt.ItemDataRole.UserRole, entry.batch_id)
            self.files.setItem(index, COL_NAME, name)
            self.files.setItem(index, COL_ROWS, read_only(QTableWidgetItem(str(entry.rows))))
            self.files.setCellWidget(index, COL_STATE, view_cell(ENTRY_VIEWS[entry.state]))
            if entry.batch_id == self._batch:
                self.files.selectRow(index)
        self.files.blockSignals(False)
        self._loading = False
        self.count_label.setText(str(len(entries)))
        self._load_rows()

    def _file_selected(self) -> None:
        items = self.files.selectedItems()
        if self._loading or not items:
            return
        batch = items[0].data(Qt.ItemDataRole.UserRole)
        if batch is not None and int(batch) != self._batch:
            self._batch = int(batch)
            self._page = 0
            self._load_rows()

    def _load_rows(self) -> None:
        entry = self._chosen()
        batch = self._batch
        if entry is None or batch is None:
            self._rows = []
        else:
            self._rows = self.ctx.read(lambda conn: csv_batches.csv_rows(conn, batch))
        self.title_label.setText(entry.name if entry else "Нет собранных CSV")
        self.subtitle.setText(rows_text(entry) if entry else "Загрузите файлы по SFTP и нажмите «+ CSV»")
        self._fill_rows()
        self._sync_buttons(entry)

    def _fill_rows(self) -> None:
        sort = row_sort_key(self.row_sorter.field)
        rows = sorted(self._rows, key=sort, reverse=self.row_sorter.descending)
        last_page = max(0, (len(rows) - 1) // ROW_PAGE_SIZE)
        self._page = min(self._page, last_page)
        start = self._page * ROW_PAGE_SIZE
        self._shown = rows[start : start + ROW_PAGE_SIZE]
        self._loading = True
        self.table.setRowCount(len(self._shown))
        for index, row in enumerate(self._shown):
            self._set_row(index, row)
        self._loading = False
        self.pager.show_page(len(rows), ROW_PAGE_SIZE, self._page)

    def _set_row(self, index: int, row: sqlite3.Row) -> None:
        locked = row["status"] == state.SUBMITTED  # a file already sent is final
        name = read_only(QTableWidgetItem(row["filename"]))
        name.setFont(mono_font())
        cells = (
            name,
            QTableWidgetItem(row["title"]),
            QTableWidgetItem(", ".join(json.loads(row["keywords"]))),
            QTableWidgetItem(category_name(row["category"])),
            read_only(QTableWidgetItem("")),  # the CSV leaves Releases empty
        )
        for column, cell in enumerate(cells):
            self.table.setItem(index, column, read_only(cell) if locked else cell)

    def _go_page(self, number: int) -> None:
        self._page = number
        self._fill_rows()

    def _sync_buttons(self, entry: csv_batches.CsvEntry | None) -> None:
        idle = not self.busy
        self.add_button.setEnabled(idle and bool(self._offered))
        self.add_hint.setVisible(not self._offered)
        self.build_button.setEnabled(idle and entry is not None and entry.state != csv_batches.SENT)
        self.sent_button.setEnabled(idle and entry is not None and entry.state == csv_batches.BUILT)

    def _set_running(self, running: bool) -> None:
        super()._set_running(running)
        if not running:
            self._sync_buttons(self._chosen())

    # ------------------------------------------------------------------ editing

    def _cell_edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() not in EDITABLE_COLUMNS or not 0 <= item.row() < len(self._shown):
            return
        cells = [self.table.item(item.row(), column) for column in EDITABLE_COLUMNS]
        if self._batch is None or any(cell is None for cell in cells):
            return
        title, keywords, category = (cell.text() for cell in cells if cell is not None)
        filename = self._shown[item.row()]["filename"]
        self._save(self._batch, filename, title.strip(), parse_keywords(keywords), category_code(category))

    def _save(self, batch: int, filename: str, title: str, keywords: tuple[str, ...], category: str) -> None:
        """Write the edit to the database, then rewrite the batch's CSV so the two never differ."""
        try:
            settings = self.ctx.prefs.build_settings(with_password=False)
        except (ValidationError, ValueError):
            settings = None

        def work(conn, progress, should_stop):
            saved = state.update_metadata(conn, filename, title, keywords, category)
            if saved and settings is not None:
                services.build_csv(conn, batch, settings)
            return saved

        try:
            saved = self.ctx.job(work)(lambda *_: None, lambda: False)
        except Exception as error:  # a locked or damaged database: show it, keep the edit on screen
            self.status.setText(f"Не удалось сохранить {filename}: {error}")
            return
        if not saved:
            self.status.setText(f"Не сохранено: {filename} уже отправлен, его не правят.")
        elif settings is None:
            self.status.setText(f"Сохранено в базе: {filename}. CSV не пересобран: проверьте настройки.")
        else:
            self.status.setText(f"Сохранено: {filename}")
        QTimer.singleShot(0, self.ctx.data_changed.emit)  # not inside the table's own signal

    # ------------------------------------------------------------------ actions

    def _add(self) -> None:
        offered = self._offered
        if not offered:
            return
        labels = [
            f"Батч {r['batch_id']}: {r['folder']}, часть {r['part']} — {r['n']} файлов" for r in offered
        ]
        label, accepted = QInputDialog.getItem(self, "Новый CSV", "Батч", labels, len(labels) - 1, False)
        if accepted:
            self._build(int(offered[labels.index(label)]["batch_id"]))

    def _build(self, batch: int | None) -> None:
        if batch is None:
            return
        try:
            settings = self.ctx.prefs.build_settings(with_password=False)
        except (ValidationError, ValueError) as error:
            self.status.setText(f"Проверьте настройки: {error}")
            return

        def work(conn, progress, should_stop):
            return services.build_csv(conn, batch, settings)

        self.start(work, self._built)

    def _built(self, report: object) -> None:
        if not isinstance(report, services.CsvReport):
            return
        self._batch = report.batch
        self._csv_dir = report.path.parent if report.path else None
        self.refresh()
        if report.empty:
            self.status.setText("В батче нет загруженных и неотправленных файлов.")
        elif report.problems:
            self.status.setText("ПРОБЛЕМА CSV: " + "; ".join(report.problems))
        else:
            self.status.setText(
                f"CSV собран: {report.rows} строк, проверен по правилам Adobe. "
                f"«People and Property are fictional» — {report.people} файлов с людьми. {PORTAL_STEPS}"
            )

    def _open_folder(self) -> None:
        folder = self._csv_dir or self.ctx.prefs.csv_dir
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _submitted(self) -> None:
        batch = self._batch
        if batch is None:
            return
        answer = QMessageBox.question(
            self,
            "Отметить отправленным",
            f"Вы уже загрузили CSV батча {batch} в портале и отправили файлы на проверку?\n"
            "Батч будет помечен как отправленный.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def work(conn, progress, should_stop):
            return services.mark_submitted(conn, batch)

        self.start(work, self._marked)

    def _marked(self, report: object) -> None:
        if not isinstance(report, services.SubmittedReport):
            return
        warnings = " ".join(f"ВНИМАНИЕ {warning}." for warning in report.warnings)
        self.refresh()
        self.status.setText(f"Отмечено отправленными: {report.count}. {warnings}".strip())
