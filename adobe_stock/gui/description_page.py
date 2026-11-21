"""Step 2: files with their titles, keywords and categories (list or tiles) and an editor on the right."""

import json
import sqlite3
from typing import cast

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from adobe_stock import caption, services, state
from adobe_stock.categories import CATEGORIES
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.file_actions import FILE_PATH_ROLE, attach_file_menu, open_file
from adobe_stock.gui.header_sort import HeaderSort
from adobe_stock.gui.pager import Pager, pages_in
from adobe_stock.gui.status import drop_cell_widgets, status_cell, view_of
from adobe_stock.gui.tag_editor import TagEditor, parse_keywords
from adobe_stock.gui.task_page import TaskPage
from adobe_stock.gui.thumbnails import thumbnail_pixmap, tile_icon
from adobe_stock.gui.widgets import make_combo, mono_font

ROW_THUMB = 40
TILE_THUMB = 120
PREVIEW_SIDE = 240
PANEL_WIDTH = 320
LIST_PAGE_SIZE = 20
TILES_PAGE_SIZE = 60
BYTES_IN_MB = 1024 * 1024
COLUMNS = ("Имя", "Заголовок", "Ключевые слова", "Категория", "Размер", "Статус")
COL_NAME, COL_TITLE, COL_KEYWORDS, COL_CATEGORY, COL_SIZE, COL_STATUS = range(len(COLUMNS))
HEADER_FIELDS = ("name", "title", "keywords", "category", "size", "status")  # the sort field of each column
FILTER_ALL = "all"


def category_name(code: str) -> str:
    """The category's name for a stored code; empty when none is set (or the code is unknown)."""
    return CATEGORIES.get(int(code), "") if code.isdigit() else ""


def format_size(size: int) -> str:
    return f"{size / BYTES_IN_MB:.1f}".replace(".", ",") + " МБ"


def sort_key(field: str):
    if field == "name":
        return lambda row: row["filename"].lower()
    if field == "title":
        return lambda row: (row["title"] == "", row["title"].lower())  # empty ones go last
    if field == "keywords":
        return lambda row: (row["keywords"] == "[]", row["keywords"].lower())
    if field == "category":
        return lambda row: (not row["category"], category_name(row["category"]).lower())
    if field == "size":
        return lambda row: row["size"]
    return lambda row: (view_of(row["status"]).rank, row["filename"].lower())


def clean_button(text: str, **properties: bool) -> QPushButton:
    button = QPushButton(text)
    for name, value in properties.items():
        button.setProperty(name, value)
    return button


class DescriptionPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "")
        self._rows: list[sqlite3.Row] = []
        self._visible: list[sqlite3.Row] = []
        self._selected = ""
        self._page = 0
        self._loading = False
        self.output.setVisible(False)
        self.cancel_button.setVisible(False)  # shown only while an operation runs
        self.output.setMaximumHeight(100)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._build_files_panel(), 1)
        body.addWidget(self._build_meta_panel())
        self.form.addLayout(body)
        outer = cast(QVBoxLayout, self.layout())
        outer.setStretchFactor(self.form, 1)

    # ---- construction -------------------------------------------------------------------------

    def _build_files_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        column = QVBoxLayout(panel)
        column.setContentsMargins(12, 12, 12, 8)

        head = QHBoxLayout()
        heading = QLabel("Файлы")
        heading.setProperty("role", "heading")
        head.addWidget(heading)
        self.model = make_combo()
        for model_id, note in caption.MODELS:
            self.model.addItem(model_id, model_id)
            self.model.setItemData(
                self.model.count() - 1, f"{model_id} — {note}", Qt.ItemDataRole.ToolTipRole
            )
        self.model.setCurrentIndex(max(self.model.findData(self.ctx.prefs.model), 0))
        head.addWidget(self.model)
        self.run_button = self.add_action(QPushButton("✦ Caption"))
        self.run_button.setToolTip("Заголовок, ключевые слова и категория для всех файлов без описания")
        self.run_button.clicked.connect(self._run_caption)
        head.addWidget(self.run_button)
        self.approve_all_button = self.add_action(QPushButton("Одобрить все на проверке"))
        self.approve_all_button.clicked.connect(lambda: self._approve(None))
        head.addWidget(self.approve_all_button)
        head.addStretch(1)

        self.status_filter = make_combo()
        self.status_filter.addItem("Все статусы", FILTER_ALL)
        for key in (state.NEEDS_METADATA, state.PENDING):
            self.status_filter.addItem(view_of(key).label, key)
        self.status_filter.currentIndexChanged.connect(self._filters_changed)
        head.addWidget(self.status_filter)
        self.list_button = clean_button("Список", segment=True)
        self.tiles_button = clean_button("Плитки", segment=True)
        for button in (self.list_button, self.tiles_button):
            button.setCheckable(True)
        self.list_button.setChecked(True)
        self.list_button.clicked.connect(lambda: self._set_view(tiles=False))
        self.tiles_button.clicked.connect(lambda: self._set_view(tiles=True))
        head.addWidget(self.list_button)
        head.addWidget(self.tiles_button)
        column.addLayout(head)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setIconSize(QSize(ROW_THUMB, ROW_THUMB))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_THUMB + 8)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_TITLE, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            COL_KEYWORDS, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.setColumnWidth(COL_NAME, 190)
        self.table.setColumnWidth(COL_CATEGORY, 150)
        self.table.setColumnWidth(COL_SIZE, 80)
        self.table.setColumnWidth(COL_STATUS, 110)
        self.table.itemSelectionChanged.connect(self._table_selected)
        self.table.itemChanged.connect(self._cell_edited)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.sorter = HeaderSort(
            self.table,
            COLUMNS,
            HEADER_FIELDS,
            field="status",
            descending=False,
            on_change=self._filters_changed,
        )

        self.tiles = QListWidget()
        self.tiles.setObjectName("tiles")
        self.tiles.setViewMode(QListView.ViewMode.IconMode)
        self.tiles.setResizeMode(QListView.ResizeMode.Adjust)
        self.tiles.setMovement(QListView.Movement.Static)
        self.tiles.setIconSize(QSize(TILE_THUMB, TILE_THUMB))
        self.tiles.setGridSize(QSize(TILE_THUMB + 24, TILE_THUMB + 36))
        self.tiles.setWordWrap(True)
        self.tiles.setSpacing(4)
        self.tiles.itemSelectionChanged.connect(self._tile_selected)
        self.tiles.itemDoubleClicked.connect(lambda item: open_file(item.data(FILE_PATH_ROLE) or ""))
        attach_file_menu(self.tiles, lambda pos: self._path_of(self.tiles.itemAt(pos)))
        attach_file_menu(self.table, lambda pos: self._path_of(self._name_item(self.table.itemAt(pos))))
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)

        self.views = QStackedWidget()
        self.views.addWidget(self.table)
        self.views.addWidget(self.tiles)
        column.addWidget(self.views, 1)

        self.pager = Pager()
        self.pager.page_selected.connect(self._go_page)
        column.addWidget(self.pager)
        return panel

    def _build_meta_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(16, 12, 16, 12)
        column.setSpacing(8)
        heading = QLabel("Метаданные")
        heading.setProperty("role", "heading")
        column.addWidget(heading)
        self.preview = QLabel("Выберите файл")
        self.preview.setProperty("role", "preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(PREVIEW_SIDE * 3 // 4)
        column.addWidget(self.preview)
        self.name_label = QLabel("")
        self.name_label.setProperty("role", "path")
        self.name_label.setWordWrap(True)
        column.addWidget(self.name_label)
        self.info_label = QLabel("")
        self.info_label.setProperty("role", "muted")
        column.addWidget(self.info_label)
        self.reason_label = QLabel("")
        self.reason_label.setProperty("role", "warn")
        self.reason_label.setWordWrap(True)
        column.addWidget(self.reason_label)

        column.addWidget(self._field_label("Заголовок"))
        self.title_edit = QLineEdit()
        self.title_edit.textChanged.connect(self._update_dirty)
        column.addWidget(self.title_edit)
        self.keywords_edit = TagEditor()
        self.keywords_edit.changed.connect(self._update_dirty)
        column.addWidget(self.keywords_edit)
        column.addWidget(self._field_label("Категория"))
        self.category_box = make_combo()
        self.category_box.addItem("—", "")
        for code, name in CATEGORIES.items():
            self.category_box.addItem(name, str(code))
        self.category_box.currentIndexChanged.connect(self._update_dirty)
        column.addWidget(self.category_box)

        self.dirty_label = QLabel("")
        self.dirty_label.setProperty("role", "warn")
        column.addWidget(self.dirty_label)
        buttons = QHBoxLayout()
        self.revert_button = QPushButton("Отменить")
        self.revert_button.clicked.connect(self._load_panel)
        self.save_button = QPushButton("Сохранить")
        self.save_button.setProperty("primary", True)
        self.save_button.clicked.connect(self._save_panel)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.save_button)
        column.addLayout(buttons)
        self.approve_button = QPushButton("Одобрить файл")
        self.approve_button.clicked.connect(lambda: self._approve([self._selected]))
        column.addWidget(self.approve_button)
        column.addStretch(1)
        nav = QHBoxLayout()
        self.prev_button = QPushButton("← Пред")
        self.prev_button.clicked.connect(lambda: self._step(-1))
        self.next_button = QPushButton("След →")
        self.next_button.clicked.connect(lambda: self._step(1))
        nav.addWidget(self.prev_button)
        nav.addWidget(self.next_button)
        column.addLayout(nav)
        return panel

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "muted")
        return label

    def _set_running(self, running: bool) -> None:
        super()._set_running(running)
        self.cancel_button.setVisible(running)

    def write(self, text: str) -> None:
        self.output.setVisible(True)
        super().write(text)

    # ---- data ---------------------------------------------------------------------------------

    def refresh(self) -> None:
        try:
            # only what can still be described: invalid files stay on the scan page, files in a batch are
            # planned, uploading or sent already
            self._rows = self.ctx.read(state.files_to_describe)
        except services.StateDbError as error:
            self.status.setText(str(error))
            return
        review = sum(1 for row in self._rows if row["status"] == state.REVIEW)
        self.approve_all_button.setVisible(review > 0)
        self.approve_all_button.setText(f"Одобрить все на проверке ({review})")
        self._rebuild()

    def _page_size(self) -> int:
        return TILES_PAGE_SIZE if self.tiles_button.isChecked() else LIST_PAGE_SIZE

    def _rebuild(self) -> None:
        wanted = self.status_filter.currentData()
        rows = [row for row in self._rows if wanted == FILTER_ALL or row["status"] == wanted]
        rows.sort(key=sort_key(self.sorter.field), reverse=self.sorter.descending)
        self._visible = rows
        size = self._page_size()
        if self._selected and not self._page_changed_by_user:
            names = [row["filename"] for row in rows]
            if self._selected in names:
                self._page = names.index(self._selected) // size
        self._page = min(self._page, pages_in(len(rows), size) - 1)
        self._fill_views()
        self._fill_pager()
        self._load_panel()

    _page_changed_by_user = False

    def _current_rows(self) -> list[sqlite3.Row]:
        size = self._page_size()
        return self._visible[self._page * size : (self._page + 1) * size]

    def _fill_views(self) -> None:
        rows = self._current_rows()
        self._loading = True
        try:
            drop_cell_widgets(self.table, COL_STATUS)
            self.table.setRowCount(len(rows))
            self.tiles.clear()
            for index, row in enumerate(rows):
                if self.tiles_button.isChecked():
                    self._add_tile(row)
                else:
                    self._set_table_row(index, row)
        finally:
            self._loading = False
        self._select_in_views()

    def _name_item(self, item):
        return self.table.item(item.row(), COL_NAME) if item is not None else None

    @staticmethod
    def _path_of(item) -> str:
        return (item.data(FILE_PATH_ROLE) or "") if item is not None else ""

    def _cell_double_clicked(self, row: int, column: int) -> None:
        if column == COL_NAME:  # the other cells are edited on double-click
            open_file(self._path_of(self.table.item(row, COL_NAME)))

    def _set_table_row(self, index: int, row: sqlite3.Row) -> None:
        name = QTableWidgetItem(row["filename"])
        name.setIcon(tile_icon(row["path"], ROW_THUMB))
        name.setToolTip(row["path"])
        name.setData(FILE_PATH_ROLE, row["path"])
        name.setFont(mono_font())
        name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(index, COL_NAME, name)
        self.table.setItem(index, COL_TITLE, QTableWidgetItem(row["title"]))
        self.table.setItem(index, COL_KEYWORDS, QTableWidgetItem(", ".join(json.loads(row["keywords"]))))
        size = QTableWidgetItem(format_size(row["size"]))
        size.setFlags(size.flags() & ~Qt.ItemFlag.ItemIsEditable)
        category = QTableWidgetItem(category_name(row["category"]))
        category.setFlags(category.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(index, COL_CATEGORY, category)
        self.table.setItem(index, COL_SIZE, size)
        status = QTableWidgetItem()  # the chip below is the visible part; the label stays readable here
        status.setData(Qt.ItemDataRole.AccessibleTextRole, view_of(row["status"]).label)
        status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(index, COL_STATUS, status)
        self.table.setCellWidget(index, COL_STATUS, status_cell(row["status"]))

    def _add_tile(self, row: sqlite3.Row) -> None:
        view = view_of(row["status"])
        item = QListWidgetItem(
            tile_icon(row["path"], TILE_THUMB, view.token), f"{row['filename']}\n{view.label}"
        )
        item.setData(Qt.ItemDataRole.UserRole, row["filename"])
        item.setData(FILE_PATH_ROLE, row["path"])
        item.setToolTip(f"{row['filename']}\n{view.label}")
        self.tiles.addItem(item)

    def _fill_pager(self) -> None:
        self.pager.show_page(len(self._visible), self._page_size(), self._page)

    def _go_page(self, number: int) -> None:
        self._page = number
        self._page_changed_by_user = True
        try:
            self._rebuild()
        finally:
            self._page_changed_by_user = False

    # ---- view controls ------------------------------------------------------------------------

    def _filters_changed(self) -> None:
        self._page = 0
        self._rebuild()

    def _set_view(self, tiles: bool) -> None:
        self.tiles_button.setChecked(tiles)
        self.list_button.setChecked(not tiles)
        self.views.setCurrentIndex(1 if tiles else 0)
        self._page = 0
        self._rebuild()

    # ---- selection ----------------------------------------------------------------------------

    def _table_selected(self) -> None:
        if self._loading:
            return
        row = self.table.currentRow()
        if 0 <= row < len(self._current_rows()):
            self._choose(self._current_rows()[row]["filename"])

    def _tile_selected(self) -> None:
        if self._loading:
            return
        items = self.tiles.selectedItems()
        if items:
            self._choose(str(items[0].data(Qt.ItemDataRole.UserRole)))

    def _choose(self, filename: str) -> None:
        if filename == self._selected:
            return
        if self._is_dirty() and not self._confirm_discard():
            self._select_in_views()
            return
        self._selected = filename
        self._load_panel()

    def _confirm_discard(self) -> bool:
        answer = QMessageBox.question(
            self, "Есть несохранённые изменения", "Отбросить правки в панели «Метаданные»?"
        )
        return answer == QMessageBox.StandardButton.Yes

    def _select_in_views(self) -> None:
        names = [row["filename"] for row in self._current_rows()]
        index = names.index(self._selected) if self._selected in names else -1
        was_loading, self._loading = self._loading, True
        try:
            if index < 0:
                self.table.clearSelection()
                self.tiles.clearSelection()
            else:
                self.table.selectRow(index)
                self.tiles.setCurrentRow(index)
        finally:
            self._loading = was_loading

    def _step(self, delta: int) -> None:
        names = [row["filename"] for row in self._visible]
        if not names:
            return
        position = names.index(self._selected) + delta if self._selected in names else 0
        target = names[max(0, min(len(names) - 1, position))]
        if target == self._selected:
            return
        if self._is_dirty() and not self._confirm_discard():
            return
        self._selected = target
        self._page = names.index(target) // self._page_size()
        self._fill_views()
        self._fill_pager()
        self._load_panel()

    # ---- metadata panel -----------------------------------------------------------------------

    def _selected_row(self) -> sqlite3.Row | None:
        return next((row for row in self._rows if row["filename"] == self._selected), None)

    def _load_panel(self) -> None:
        row = self._selected_row()
        was_loading, self._loading = self._loading, True
        try:
            enabled = row is not None
            for widget in (self.title_edit, self.keywords_edit, self.category_box):
                widget.setEnabled(enabled)
            if row is None:
                self._selected = ""
                self.preview.setPixmap(thumbnail_pixmap("", 1))
                self.preview.setText("Выберите файл")
                for label in (self.name_label, self.info_label, self.reason_label):
                    label.setText("")
                self.title_edit.clear()
                self.keywords_edit.set_tags(())
                self.category_box.setCurrentIndex(0)
            else:
                self._show_row(row)
        finally:
            self._loading = was_loading
        self._update_dirty()

    def _show_row(self, row: sqlite3.Row) -> None:
        pixmap = thumbnail_pixmap(row["path"], PREVIEW_SIDE)
        if pixmap.isNull():
            self.preview.setPixmap(pixmap)
            self.preview.setText("Нет превью")
        else:
            self.preview.setPixmap(pixmap)
        self.name_label.setText(row["filename"])
        view = view_of(row["status"])
        self.info_label.setText(f"{format_size(row['size'])} · {view.label}")
        self.reason_label.setText(row["error"] or "")
        self.title_edit.setText(row["title"])
        self.keywords_edit.set_tags(json.loads(row["keywords"]))
        self.category_box.setCurrentIndex(max(0, self.category_box.findData(row["category"])))

    def _panel_values(self) -> tuple[str, tuple[str, ...], str]:
        return (
            self.title_edit.text().strip(),
            self.keywords_edit.tags(),
            str(self.category_box.currentData()),
        )

    def _is_dirty(self) -> bool:
        row = self._selected_row()
        if row is None:
            return False
        return self._panel_values() != (row["title"], tuple(json.loads(row["keywords"])), row["category"])

    def _update_dirty(self) -> None:
        if self._loading:
            return
        dirty = self._is_dirty()
        self.dirty_label.setText("● Есть несохранённые изменения" if dirty else "")
        self.save_button.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)
        row = self._selected_row()
        self.approve_button.setVisible(row is not None and row["status"] == state.REVIEW)
        names = [r["filename"] for r in self._visible]
        index = names.index(self._selected) if self._selected in names else -1
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(0 <= index < len(names) - 1)

    def _save_panel(self) -> None:
        title, keywords, category = self._panel_values()
        self._save(self._selected, title, keywords, category)

    def _cell_edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() not in (COL_TITLE, COL_KEYWORDS):
            return
        rows = self._current_rows()
        if not 0 <= item.row() < len(rows):
            return
        row = rows[item.row()]
        title_item = self.table.item(item.row(), COL_TITLE)
        keywords_item = self.table.item(item.row(), COL_KEYWORDS)
        if title_item is None or keywords_item is None:
            return
        title = title_item.text().strip()
        keywords = parse_keywords(keywords_item.text())
        self._save(row["filename"], title, keywords, row["category"])

    def _save(self, filename: str, title: str, keywords: tuple[str, ...], category: str) -> None:
        def work(conn, progress, should_stop):
            return state.update_metadata(conn, filename, title, keywords, category)

        try:
            saved = self.ctx.job(work)(lambda *_: None, lambda: False)
        except Exception as error:  # a locked or damaged database: show it, keep the edit on screen
            self.write(f"ОШИБКА: не удалось сохранить {filename}: {error}")
            return
        if not saved:
            self.write(f"Не сохранено: {filename} уже в батче или отправлен, его не описывают повторно.")
            QTimer.singleShot(0, self.ctx.data_changed.emit)
            return
        self.status.setText(f"Сохранено: {filename}")
        QTimer.singleShot(0, self.ctx.data_changed.emit)  # not inside the table's own signal

    # ---- long operations ----------------------------------------------------------------------

    def _run_caption(self) -> None:
        if caption.find_claude() is None:
            QMessageBox.warning(
                self,
                "Claude Code не найден",
                "Установите Claude Code и войдите в аккаунт, либо укажите путь к `claude` на вкладке "
                "«Настройки» и нажмите «Проверить Claude».",
            )
            return
        model = str(self.model.currentData())
        self.ctx.prefs.set_text("model", model)
        batch_size = self.ctx.prefs.batch_size

        def work(conn, progress, should_stop):
            return services.caption_files(
                conn,
                model,
                limit=services.DEFAULT_CAPTION_LIMIT,
                per_call=services.DEFAULT_IMAGES_PER_CALL,
                batch_size=batch_size,
                on_progress=progress,
                should_stop=should_stop,
            )

        self.start(work, self._caption_done)

    def _caption_done(self, report: object) -> None:
        if not isinstance(report, services.CaptionReport):
            return
        if report.total == 0:
            self.write("Нечего подписывать: нет файлов, ожидающих метаданные.")
            self.status.setText("Нечего подписывать")
            return
        self.write(
            f"Подписано: {report.captioned} из {report.total}\nНа проверке: {len(report.flagged)}\n"
            f"Новых батчей: {report.new_batches}" + ("\nОтменено пользователем" if report.stopped else "")
        )
        for message in report.flagged:
            self.write(f"ПРОВЕРКА {message}")
        if report.flagged:
            self.write("Эти файлы не пойдут в загрузку, пока вы не одобрите их (статус «Проверить»).")
        for message in report.errors:
            self.write(f"ОШИБКА {message}")
        self.status.setText("Готово" if not report.errors else "Готово, есть ошибки")

    def _approve(self, names: list[str] | None) -> None:
        batch_size = self.ctx.prefs.batch_size

        def work(conn, progress, should_stop):
            return services.approve(conn, names, batch_size)

        self.start(work, self._approve_done)

    def _approve_done(self, report: object) -> None:
        if isinstance(report, services.ApproveReport):
            self.write(f"Одобрено файлов: {report.count}, новых батчей: {report.new_batches}")
            self.status.setText(f"Одобрено: {report.count}")
