"""Step 2: files with their titles, keywords and categories (list or tiles) and an editor on the right."""

import json
import sqlite3
from typing import cast

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTableWidgetSelectionRange,
    QToolButton,
    QToolTip,
    QVBoxLayout,
)

from adobe_stock import caption, services, state
from adobe_stock.categories import CATEGORIES
from adobe_stock.gui.bulk_bar import BulkBar
from adobe_stock.gui.category_delegate import CategoryDelegate, category_code
from adobe_stock.gui.check_header import install_check_header
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.empty_cell import EmptyCellDelegate
from adobe_stock.gui.file_actions import FILE_PATH_ROLE, attach_file_menu, open_file, open_on_double_click
from adobe_stock.gui.header_sort import HeaderSort
from adobe_stock.gui.pager import Pager, pages_in
from adobe_stock.gui.status import chip, chip_html, dot_icon, drop_cell_widgets, status_cell, view_of
from adobe_stock.gui.tag_editor import TagEditor, parse_keywords
from adobe_stock.gui.task_page import TaskPage
from adobe_stock.gui.thumbnails import image_size, thumbnail_pixmap, tile_icon
from adobe_stock.gui.view_icons import grid_icon, list_icon
from adobe_stock.gui.widgets import ElidedLabel, ScrollBody, make_combo, mono_font

THUMB_SIZES = {"S": (24, 80), "M": (40, 120), "L": (72, 180)}  # (list row, tile) picture side
DEFAULT_THUMB_SIZE = "M"
THUMB_SIZE_KEY = "thumb_size"
ROW_PADDING = 8
AUTOSAVE_DELAY_MS = 700  # a pause in typing the title that saves it
NAME_COLUMN_BASE = 150  # room for the checkbox and a file name; the picture comes on top of it
PREVIEW_SIDE = 240
PANEL_WIDTH = 320
LIST_PAGE_SIZE = 20
TILES_PAGE_SIZE = 60
BYTES_IN_MB = 1024 * 1024
COLUMNS = ("Имя", "Заголовок", "Ключевые слова", "Категория", "Разрешение", "Размер", "Статус")
COL_NAME, COL_TITLE, COL_KEYWORDS, COL_CATEGORY, COL_RESOLUTION, COL_SIZE, COL_STATUS = range(len(COLUMNS))
HEADER_FIELDS = (
    "name",
    "title",
    "keywords",
    "category",
    "resolution",
    "size",
    "status",
)  # the sort field of each column
FILTER_ALL = "all"


def category_name(code: str) -> str:
    """The category's name for a stored code; empty when none is set (or the code is unknown)."""
    return CATEGORIES.get(int(code), "") if code.isdigit() else ""


def resolution_text(path: str) -> str:
    """Pixel size of the picture ("6000 × 4000 px"); empty when it cannot be read."""
    width, height = image_size(path)
    return f"{width} × {height} px" if width and height else ""


def format_size(size: int) -> str:
    return f"{size / BYTES_IN_MB:.1f}".replace(".", ",") + " МБ"


def _resolution_key(row: sqlite3.Row) -> tuple[int, int]:
    """More pixels sort later; unreadable files (0 pixels) come first."""
    width, height = image_size(row["path"])
    return (width * height, width)


def sort_key(field: str):
    if field == "name":
        return lambda row: row["filename"].lower()
    if field == "title":
        return lambda row: (row["title"] == "", row["title"].lower())  # empty ones go last
    if field == "keywords":
        return lambda row: (row["keywords"] == "[]", row["keywords"].lower())
    if field == "category":
        return lambda row: (not row["category"], category_name(row["category"]).lower())
    if field == "resolution":
        return _resolution_key
    if field == "size":
        return lambda row: row["size"]
    return lambda row: (view_of(row["status"]).rank, row["filename"].lower())


def clean_button(text: str, **properties: bool) -> QPushButton:
    button = QPushButton(text)
    for name, value in properties.items():
        button.setProperty(name, value)
    return button


def caption_help() -> str:
    """How Caption works and for which statuses; the names are the ones the status chips show."""
    new, described, review, uploaded, sent = (
        f"<b>{view_of(key).label}</b>"
        for key in (
            state.NEEDS_METADATA,
            state.PENDING,
            state.REVIEW,
            state.UPLOADED,
            state.SUBMITTED,
        )
    )
    return (
        "<b>✦ Caption</b>: Claude пишет заголовок, ключевые слова и категорию.<br>"
        f"• Ничего не отмечено: подписываются все файлы со статусом {new}.<br>"
        f"• Отмечены файлы: подписываются они. {described} и {review} подписываются заново, "
        "прежний текст заменяется (приложение спросит).<br>"
        f"• {uploaded} и {sent} не подписываются никогда."
    )


class DescriptionPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "")
        self._rows: list[sqlite3.Row] = []
        self._visible: list[sqlite3.Row] = []
        self._selected = ""
        self._shown_file = ""  # the file whose values the card shows
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DELAY_MS)
        self._autosave_timer.timeout.connect(self._autosave)
        self._marked: set[str] = set()  # the files picked for a bulk edit, across pages
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
        self.model = self._make_model_combo()
        head.addWidget(self.model)
        self.run_button = self.add_action(QPushButton("✦ Caption"))
        self.run_button.setToolTip(caption_help())
        self.run_button.clicked.connect(lambda: self._run_caption())
        head.addWidget(self.run_button)
        self.caption_info = QToolButton()  # flat and small: the same text, for those who do not hover
        self.caption_info.setText("ⓘ")
        self.caption_info.setAutoRaise(True)
        self.caption_info.setToolTip(caption_help())
        self.caption_info.setAccessibleName("Как работает Caption")
        self.caption_info.clicked.connect(
            lambda: QToolTip.showText(QCursor.pos(), caption_help(), self.caption_info)
        )
        head.addWidget(self.caption_info)
        self.approve_all_button = self.add_action(QPushButton("Approve"))
        self.approve_all_button.setToolTip("Одобрить все файлы со статусом «Проверить»")
        self.approve_all_button.clicked.connect(lambda: self._approve(None))
        head.addWidget(self.approve_all_button)
        head.addStretch(1)  # the filter and the view switches sit at the right edge

        self.status_filter = make_combo()
        self.status_filter.addItem("Все статусы", FILTER_ALL)
        for key in (state.NEEDS_METADATA, state.PENDING, state.REVIEW, state.UPLOADED):
            self.status_filter.addItem(view_of(key).label, key)
        self.status_filter.currentIndexChanged.connect(self._filters_changed)
        head.addWidget(self.status_filter)
        saved_size = self.ctx.prefs.text(THUMB_SIZE_KEY, DEFAULT_THUMB_SIZE)
        self._thumb_size = saved_size if saved_size in THUMB_SIZES else DEFAULT_THUMB_SIZE
        self.size_buttons: dict[str, QPushButton] = {}
        sizes = self._joined_group()
        for key in THUMB_SIZES:
            button = clean_button(key, segment=True, compact=True)
            button.setCheckable(True)
            button.setChecked(key == self._thumb_size)
            button.setToolTip({"S": "Мелкие превью", "M": "Средние превью", "L": "Крупные превью"}[key])
            button.clicked.connect(lambda _checked=False, size=key: self._set_thumb_size(size))
            self.size_buttons[key] = button
            sizes.addWidget(button)
        self._join(sizes)
        head.addLayout(sizes)
        self.list_button = clean_button("", segment=True, compact=True)
        self.list_button.setIcon(list_icon())
        self.list_button.setToolTip("Список")
        self.tiles_button = clean_button("", segment=True, compact=True)
        self.tiles_button.setIcon(grid_icon())
        self.tiles_button.setToolTip("Плитки")
        for button in (self.list_button, self.tiles_button):
            button.setCheckable(True)
        self.list_button.setChecked(True)
        self.list_button.clicked.connect(lambda: self._set_view(tiles=False))
        self.tiles_button.clicked.connect(lambda: self._set_view(tiles=True))
        views = self._joined_group()
        views.addWidget(self.list_button)
        views.addWidget(self.tiles_button)
        self._join(views)
        head.addLayout(views)
        column.addLayout(head)

        self.table = QTableWidget(0, len(COLUMNS))
        self.check_header = install_check_header(self.table)
        self.check_header.toggled.connect(self._toggle_all)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setIconSize(QSize(self._row_thumb, self._row_thumb))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self._row_thumb + ROW_PADDING)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_TITLE, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            COL_KEYWORDS, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self._fit_name_column()
        self.table.setColumnWidth(COL_CATEGORY, 150)
        self.table.setColumnWidth(COL_RESOLUTION, 120)
        self.table.setColumnWidth(COL_SIZE, 80)
        self.table.setItemDelegateForColumn(COL_CATEGORY, CategoryDelegate("Нет категории", self.table))
        self.table.setItemDelegateForColumn(COL_TITLE, EmptyCellDelegate("Нет заголовка", self.table))
        self.table.setItemDelegateForColumn(COL_KEYWORDS, EmptyCellDelegate("Нет ключевых слов", self.table))
        self.table.setColumnWidth(COL_STATUS, 130)
        self.table.itemSelectionChanged.connect(self._table_selected)
        self.table.itemChanged.connect(self._cell_edited)
        self.table.cellClicked.connect(self._status_cell_clicked)
        self.table.itemChanged.connect(self._table_check_changed)
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
        self._apply_tile_size()
        self.tiles.setWordWrap(True)
        self.tiles.setSpacing(4)
        self.tiles.itemSelectionChanged.connect(self._tile_selected)
        self.tiles.itemChanged.connect(self._tile_check_changed)
        self.tiles.itemDoubleClicked.connect(lambda item: open_file(item.data(FILE_PATH_ROLE) or ""))
        attach_file_menu(self.tiles, lambda pos: self._path_of(self.tiles.itemAt(pos)))
        attach_file_menu(self.table, lambda pos: self._path_of(self._name_item(self.table.itemAt(pos))))
        open_on_double_click(self.table, [COL_NAME])

        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tiles.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.bulk_bar = BulkBar()
        self.bulk_bar.applied.connect(self._bulk_apply)
        self.bulk_bar.toggle_all.connect(self._toggle_all)
        column.addWidget(self.bulk_bar)

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
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)
        # The fields scroll when the window is too short (e.g. while the result bar is open below),
        # instead of being squeezed on top of each other; Prev / Next stay in view underneath.
        scroll = QScrollArea()
        scroll.setObjectName("card-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        body = self.card_body = ScrollBody()
        body.setObjectName("card-body")
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        title_row = QHBoxLayout()
        heading = QLabel("Метаданные")
        heading.setProperty("role", "heading")
        title_row.addWidget(heading)
        title_row.addStretch(1)
        self.status_chip = chip(state.NEEDS_METADATA)
        self.status_chip.setVisible(False)
        title_row.addWidget(self.status_chip)
        column.addLayout(title_row)
        caption_row = QHBoxLayout()
        self.card_model = self._make_model_combo()
        caption_row.addWidget(self.card_model, 1)
        self.card_caption_button = self.add_action(QPushButton("✦ Caption"))
        self.card_caption_button.setProperty("primary", True)
        self.card_caption_button.setToolTip(caption_help())
        self.card_caption_button.clicked.connect(lambda: self._run_caption([self._selected]))
        caption_row.addWidget(self.card_caption_button)
        column.addLayout(caption_row)
        self.preview = QLabel("Выберите файл")
        self.preview.setProperty("role", "preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(PREVIEW_SIDE * 3 // 4)
        column.addWidget(self.preview)
        self.name_label = ElidedLabel("")  # a long file name is cut, never widening the card
        self.name_label.setProperty("role", "path")
        column.addWidget(self.name_label)
        self.info_label = QLabel("")
        self.info_label.setProperty("role", "muted")
        column.addWidget(self.info_label)
        self.reason_label = QLabel("")
        self.reason_label.setProperty("role", "warn")
        self.reason_label.setWordWrap(True)
        self.reason_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        column.addWidget(self.reason_label)

        column.addWidget(self._field_label("Заголовок"))
        self.title_edit = QLineEdit()
        self.title_edit.textEdited.connect(lambda _text: self._autosave_timer.start())
        self.title_edit.editingFinished.connect(self._flush_autosave)
        column.addWidget(self.title_edit)
        self.keywords_edit = TagEditor()
        self.keywords_edit.changed.connect(self._autosave)
        column.addWidget(self.keywords_edit)
        column.addWidget(self._field_label("Категория"))
        self.category_box = make_combo()
        self.category_box.addItem("—", "")
        for code, name in CATEGORIES.items():
            self.category_box.addItem(name, str(code))
        self.category_box.currentIndexChanged.connect(lambda _index: self._autosave())
        column.addWidget(self.category_box)
        column.addWidget(self._field_label("Статус"))
        self.status_box = make_combo()
        for key in state.EDITABLE_STATUSES:
            self.status_box.addItem(view_of(key).label, key)
        self.status_box.activated.connect(self._change_status)  # only the person's choice, not a reload
        column.addWidget(self.status_box)

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
        outer.addLayout(nav)
        return panel

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "muted")
        return label

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.card_body.fit_height()

    def _set_running(self, running: bool) -> None:
        super()._set_running(running)
        self.cancel_button.setVisible(running)

    def write(self, text: str) -> None:
        self.output.setVisible(True)
        super().write(text)

    # ---- data ---------------------------------------------------------------------------------

    def refresh(self) -> None:
        try:
            # new, described, flagged for review and uploaded files; invalid ones stay on the scan page
            self._rows = self.ctx.read(state.files_to_describe)
        except services.StateDbError as error:
            self.status.setText(str(error))
            return
        review = sum(1 for row in self._rows if row["status"] == state.REVIEW)
        self.approve_all_button.setVisible(review > 0)
        self.approve_all_button.setText(f"Approve ({review})")
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

    def _set_table_row(self, index: int, row: sqlite3.Row) -> None:
        name = QTableWidgetItem(row["filename"])
        name.setIcon(tile_icon(row["path"], self._row_thumb))
        name.setToolTip(row["path"])
        name.setData(FILE_PATH_ROLE, row["path"])
        name.setFont(mono_font())
        name.setFlags((name.flags() & ~Qt.ItemFlag.ItemIsEditable) | Qt.ItemFlag.ItemIsUserCheckable)
        name.setCheckState(self._check_state(row["filename"]))
        self.table.setItem(index, COL_NAME, name)
        self.table.setItem(index, COL_TITLE, QTableWidgetItem(row["title"]))
        self.table.setItem(index, COL_KEYWORDS, QTableWidgetItem(", ".join(json.loads(row["keywords"]))))
        size = QTableWidgetItem(format_size(row["size"]))
        size.setFlags(size.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(index, COL_CATEGORY, QTableWidgetItem(category_name(row["category"])))  # editable
        width, height = image_size(row["path"])
        resolution = QTableWidgetItem(f"{width} × {height}" if width and height else "")
        resolution.setFlags(resolution.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(index, COL_RESOLUTION, resolution)
        self.table.setItem(index, COL_SIZE, size)
        status = QTableWidgetItem()  # the chip below is the visible part; the label stays readable here
        status.setData(Qt.ItemDataRole.AccessibleTextRole, view_of(row["status"]).label)
        status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(index, COL_STATUS, status)
        self.table.setCellWidget(index, COL_STATUS, status_cell(row["status"]))

    def _add_tile(self, row: sqlite3.Row) -> None:
        view = view_of(row["status"])
        item = QListWidgetItem(
            tile_icon(row["path"], self._tile_thumb, view.token), f"{row['filename']}\n{view.label}"
        )
        item.setData(Qt.ItemDataRole.UserRole, row["filename"])
        item.setData(FILE_PATH_ROLE, row["path"])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(self._check_state(row["filename"]))
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
        self.bulk_bar.select_box.setVisible(tiles)
        self._page = 0
        self._rebuild()

    # ---- selection ----------------------------------------------------------------------------

    def _table_selected(self) -> None:
        if self._loading:
            return
        rows = self._current_rows()
        picked = {
            rows[i.row()]["filename"]
            for i in self.table.selectionModel().selectedRows()
            if i.row() < len(rows)
        }
        self._remark(picked)
        row = self.table.currentRow()
        if 0 <= row < len(rows):
            self._choose(rows[row]["filename"])

    def _tile_selected(self) -> None:
        if self._loading:
            return
        items = self.tiles.selectedItems()
        self._remark({str(item.data(Qt.ItemDataRole.UserRole)) for item in items})
        current = self.tiles.currentItem()
        if current is not None:
            self._choose(str(current.data(Qt.ItemDataRole.UserRole)))

    def _choose(self, filename: str) -> None:
        if filename == self._selected:
            return
        self._flush_autosave()  # what was typed in the card is saved before another file opens
        self._selected = filename
        self._load_panel()

    def _select_in_views(self) -> None:
        names = [row["filename"] for row in self._current_rows()]
        current = names.index(self._selected) if self._selected in names else -1
        was_loading, self._loading = self._loading, True
        try:
            self.table.clearSelection()
            self.tiles.clearSelection()
            for index, name in enumerate(names):
                if name in self._marked or index == current:
                    self.table.setRangeSelected(
                        QTableWidgetSelectionRange(index, 0, index, len(COLUMNS) - 1), True
                    )
                    item = self.tiles.item(index)
                    if item is not None:
                        item.setSelected(True)
            if current >= 0:
                self.table.setCurrentCell(current, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
                self.tiles.setCurrentRow(current, QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._loading = was_loading
        self._sync_checks()
        self._show_marks()

    # ---- bulk edit ----------------------------------------------------------------------------

    def _marked_count(self) -> int:
        return sum(1 for row in self._visible if row["filename"] in self._marked)

    def _marked_to_caption(self) -> list[str]:
        """Marked files that may be captioned: new, described and flagged ones, not the uploaded."""
        return [
            row["filename"]
            for row in self._visible
            if row["filename"] in self._marked and row["status"] in state.RECAPTIONABLE
        ]

    def _check_state(self, filename: str) -> Qt.CheckState:
        return Qt.CheckState.Checked if filename in self._marked else Qt.CheckState.Unchecked

    def _remark(self, picked: set[str]) -> None:
        """Selecting two or more rows (Cmd/Shift-click) marks them; one row is just the file being edited."""
        if len(picked) < 2:
            return
        self._marked = picked
        self._sync_checks()
        self._show_marks()

    def _sync_checks(self) -> None:
        was_loading, self._loading = self._loading, True
        try:
            for index, row in enumerate(self._current_rows()):
                state_now = self._check_state(row["filename"])
                for item in (self.table.item(index, COL_NAME), self.tiles.item(index)):
                    if item is not None and item.checkState() != state_now:
                        item.setCheckState(state_now)
        finally:
            self._loading = was_loading

    def _table_check_changed(self, item: QTableWidgetItem) -> None:
        if not self._loading and item.column() == COL_NAME:
            rows = self._current_rows()
            if item.row() < len(rows):
                self._toggle_mark(rows[item.row()]["filename"], item.checkState() == Qt.CheckState.Checked)

    def _tile_check_changed(self, item: QListWidgetItem) -> None:
        if not self._loading:
            self._toggle_mark(
                str(item.data(Qt.ItemDataRole.UserRole)), item.checkState() == Qt.CheckState.Checked
            )

    def _toggle_mark(self, filename: str, marked: bool) -> None:
        self._marked = self._marked | {filename} if marked else self._marked - {filename}
        self._show_marks()
        QTimer.singleShot(0, self._select_in_views)  # the marked rows are highlighted too, after the click

    def _show_marks(self) -> None:
        marked = self._marked_count()
        self.bulk_bar.set_marks(marked, len(self._visible))
        self.run_button.setText(f"✦ Caption ({len(self._marked_to_caption())})" if marked else "✦ Caption")
        if marked == 0:
            state = Qt.CheckState.Unchecked
        else:
            state = Qt.CheckState.Checked if marked == len(self._visible) else Qt.CheckState.PartiallyChecked
        self.check_header.set_state(state)

    def _toggle_all(self) -> None:
        """Mark every file of the filtered list (all pages), or unmark them when all are marked."""
        everything = {row["filename"] for row in self._visible}
        self._marked = set() if self._marked >= everything else everything
        self._select_in_views()

    def _bulk_apply(self, tag: str, category: object, status: object) -> None:
        names = [row["filename"] for row in self._visible if row["filename"] in self._marked]
        code = None if category is None else str(category)
        batch_size = self.ctx.prefs.batch_size

        def work(conn, progress, should_stop):
            edited = state.bulk_edit(conn, names, tag, code) if tag or code is not None else None
            moved = services.set_status(conn, names, str(status), batch_size).count if status else None
            return edited, moved

        try:
            result = self.ctx.job(work)(lambda *_: None, lambda: False)
            edited, moved = cast(tuple[int | None, int | None], result)
        except Exception as error:  # a locked or damaged database
            self.write(f"ОШИБКА: не удалось изменить файлы: {error}")
            return
        parts = []
        if edited is not None:
            parts.append(f"Изменено файлов: {edited} из {len(names)}")
        if moved is not None:
            parts.append(self._moved_text(moved, names, str(status)))
        self.status.setText(". ".join(parts))
        QTimer.singleShot(0, self.ctx.data_changed.emit)

    def _moved_text(self, moved: int, names: list[str], status: str) -> str:
        """What a status change did; a file without a description cannot leave "new", and that is said."""
        text = f"Статус сменён: {moved} из {len(names)}"
        if status == state.NEEDS_METADATA:
            return text
        wanted = set(names)
        without_text = sum(
            1
            for row in self._rows
            if row["filename"] in wanted and not (row["title"] and json.loads(row["keywords"]))
        )
        return f"{text} (без заголовка или ключевых слов: {without_text})" if without_text else text

    def _move_files(self, names: list[str], status: str) -> None:
        batch_size = self.ctx.prefs.batch_size
        try:
            report = self.ctx.job(
                lambda conn, progress, should_stop: services.set_status(conn, names, status, batch_size)
            )(lambda *_: None, lambda: False)
        except Exception as error:  # a locked or damaged database
            self.write(f"ОШИБКА: не удалось сменить статус: {error}")
            return
        if isinstance(report, services.ApproveReport):
            self.status.setText(self._moved_text(report.count, names, status))
        QTimer.singleShot(0, self.ctx.data_changed.emit)  # a refused change puts the old status back

    def _change_status(self, index: int) -> None:
        """The card's status list was used by the person: move the open file."""
        status = self.status_box.itemData(index)
        if self._selected and status is not None:
            self._move_files([self._selected], str(status))

    def _status_menu(self, current: str) -> QMenu:
        menu = QMenu(self)
        for key in state.EDITABLE_STATUSES:
            view = view_of(key)
            action = menu.addAction(dot_icon(view.token), view.label)
            action.setData(key)
            action.setCheckable(True)
            action.setChecked(key == current)
        return menu

    def _pick_status(self, current: str) -> str | None:
        """Show the list of statuses at the mouse; the chosen one, or None when it is closed."""
        chosen = self._status_menu(current).exec(QCursor.pos())
        return None if chosen is None else str(chosen.data())

    def _status_cell_clicked(self, row: int, column: int) -> None:
        """A click on the status of a list row opens the list of statuses, like the category cell does."""
        rows = self._current_rows()
        if column != COL_STATUS or self._loading or not 0 <= row < len(rows):
            return
        status = self._pick_status(rows[row]["status"])
        if status is not None:
            self._move_files([rows[row]["filename"]], status)

    def _step(self, delta: int) -> None:
        names = [row["filename"] for row in self._visible]
        if not names:
            return
        position = names.index(self._selected) + delta if self._selected in names else 0
        target = names[max(0, min(len(names) - 1, position))]
        if target == self._selected:
            return
        self._flush_autosave()
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
            for widget in (self.title_edit, self.keywords_edit, self.category_box, self.status_box):
                widget.setEnabled(enabled)
            if row is None:
                self._selected = ""
                self.preview.setPixmap(thumbnail_pixmap("", 1))
                self.preview.setText("Выберите файл")
                self.status_chip.setVisible(False)
                for label in (self.name_label, self.info_label, self.reason_label):
                    label.setText("")
                self.title_edit.clear()
                self.keywords_edit.set_tags(())
                self.category_box.setCurrentIndex(0)
            else:
                self._show_row(row)
        finally:
            self._loading = was_loading
        self._update_controls()
        QTimer.singleShot(0, self.card_body.fit_height)  # once the new tags have been laid out

    def _show_row(self, row: sqlite3.Row) -> None:
        pixmap = thumbnail_pixmap(row["path"], PREVIEW_SIDE)
        if pixmap.isNull():
            self.preview.setPixmap(pixmap)
            self.preview.setText("Нет превью")
        else:
            self.preview.setPixmap(pixmap)
        self.name_label.setText(row["filename"])
        self.name_label.setToolTip(row["filename"])
        self.status_chip.setText(chip_html(row["status"]))
        self.status_chip.setVisible(True)
        self.info_label.setText(f"{format_size(row['size'])} · {resolution_text(row['path'])}".rstrip(" ·"))
        self.reason_label.setText(row["error"] or "")
        same_file = row["filename"] == self._shown_file
        self._shown_file = row["filename"]
        typing = self._autosave_timer.isActive() or self.title_edit.hasFocus()
        if not (same_file and typing) and self.title_edit.text() != row["title"]:
            self.title_edit.setText(row["title"])  # a refresh never overwrites what is being typed
        stored_tags = tuple(json.loads(row["keywords"]))
        if self.keywords_edit.tags() != stored_tags:
            self.keywords_edit.set_tags(stored_tags)
        self.status_box.setCurrentIndex(max(0, self.status_box.findData(row["status"])))
        category_index = max(0, self.category_box.findData(row["category"]))
        if self.category_box.currentIndex() != category_index:
            self.category_box.setCurrentIndex(category_index)

    def _panel_values(self) -> tuple[str, tuple[str, ...], str]:
        return (
            self.title_edit.text().strip(),
            self.keywords_edit.tags(),
            str(self.category_box.currentData()),
        )

    def _stored_values(self) -> tuple[str, tuple[str, ...], str] | None:
        row = self._selected_row()
        if row is None:
            return None
        return (row["title"], tuple(json.loads(row["keywords"])), row["category"])

    def _autosave(self) -> None:
        """Save the card's fields as soon as they change (title: after a short pause in typing)."""
        self._autosave_timer.stop()
        if self._loading or not self._selected:
            return
        values = self._panel_values()
        if values == self._stored_values():
            return
        title, keywords, category = values
        self._save(self._selected, title, keywords, category)

    def _flush_autosave(self) -> None:
        """Save a title that is still waiting for its pause, e.g. before another file is opened."""
        if self._autosave_timer.isActive():
            self._autosave()

    def _update_controls(self) -> None:
        if self._loading:
            return
        row = self._selected_row()
        self.approve_button.setVisible(row is not None and row["status"] == state.REVIEW)
        self.card_caption_button.setEnabled(row is not None and row["status"] in state.RECAPTIONABLE)
        names = [r["filename"] for r in self._visible]
        index = names.index(self._selected) if self._selected in names else -1
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(0 <= index < len(names) - 1)

    def _cell_edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() not in (COL_TITLE, COL_KEYWORDS, COL_CATEGORY):
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
        category_item = self.table.item(item.row(), COL_CATEGORY)
        category = category_code(category_item.text()) if category_item is not None else row["category"]
        self._save(row["filename"], title, keywords, category)

    def _save(self, filename: str, title: str, keywords: tuple[str, ...], category: str) -> None:
        def work(conn, progress, should_stop):
            return state.update_metadata(conn, filename, title, keywords, category)

        try:
            saved = self.ctx.job(work)(lambda *_: None, lambda: False)
        except Exception as error:  # a locked or damaged database: show it, keep the edit on screen
            self.write(f"ОШИБКА: не удалось сохранить {filename}: {error}")
            return
        if not saved:
            self.write(f"Не сохранено: {filename} уже отправлен или не подлежит описанию, его не правят.")
            QTimer.singleShot(0, self.ctx.data_changed.emit)
            return
        self.status.setText(f"Сохранено: {filename}")
        QTimer.singleShot(0, self.ctx.data_changed.emit)  # not inside the table's own signal

    # ---- long operations ----------------------------------------------------------------------

    @property
    def _row_thumb(self) -> int:
        return THUMB_SIZES[self._thumb_size][0]

    @property
    def _tile_thumb(self) -> int:
        return THUMB_SIZES[self._thumb_size][1]

    @staticmethod
    def _joined_group() -> QHBoxLayout:
        group = QHBoxLayout()
        group.setSpacing(-1)  # neighbours share a border: one joined control
        return group

    @staticmethod
    def _join(group: QHBoxLayout) -> None:
        """Round only the outer corners of a row of segment buttons."""
        items = (group.itemAt(i) for i in range(group.count()))
        buttons = [widget for widget in (item.widget() for item in items if item is not None) if widget]
        for number, button in enumerate(buttons):
            joint = "first" if number == 0 else "last" if number == len(buttons) - 1 else "mid"
            button.setProperty("joint", joint)

    def _fit_name_column(self) -> None:
        self.table.setColumnWidth(COL_NAME, NAME_COLUMN_BASE + self._row_thumb)

    def _apply_tile_size(self) -> None:
        self.tiles.setIconSize(QSize(self._tile_thumb, self._tile_thumb))
        self.tiles.setGridSize(QSize(self._tile_thumb + 24, self._tile_thumb + 36))

    def _set_thumb_size(self, size: str) -> None:
        self._thumb_size = size
        self.ctx.prefs.set_text(THUMB_SIZE_KEY, size)
        for key, button in self.size_buttons.items():
            button.setChecked(key == size)
        self.table.setIconSize(QSize(self._row_thumb, self._row_thumb))
        self.table.verticalHeader().setDefaultSectionSize(self._row_thumb + ROW_PADDING)
        self._apply_tile_size()
        self._fit_name_column()
        self._fill_views()

    def _make_model_combo(self, prefix: str = "") -> QComboBox:
        """A model picker; the one in the file list and the one in the card always show the same model."""
        combo = make_combo()
        for model_id, note in caption.MODELS:
            combo.addItem(prefix + caption.model_title(model_id), model_id)
            combo.setItemData(combo.count() - 1, f"{model_id} — {note}", Qt.ItemDataRole.ToolTipRole)
        combo.setCurrentIndex(max(combo.findData(self.ctx.prefs.model), 0))
        combo.currentIndexChanged.connect(lambda index, source=combo: self._model_changed(source, index))
        return combo

    def _model_changed(self, source: QComboBox, index: int) -> None:
        for combo in (self.model, getattr(self, "card_model", None)):
            if combo is not None and combo is not source and combo.currentIndex() != index:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)

    def _run_caption(self, names: list[str] | None = None) -> None:
        """Caption `names`; without them the marked files, and with nothing marked every new file.

        Picked files that already have a description are captioned again, after the person agrees.
        """
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
        # nothing marked: every new file; something marked: only the marked ones that may be captioned
        targets = (
            names if names is not None else (self._marked_to_caption() if self._marked_count() else None)
        )
        redo = targets is not None  # picked files that already have a description are replaced
        described = self._described_among(targets)
        if described and not self._confirm_overwrite(described):
            return

        ai_label = self.ctx.prefs.build_settings(with_password=False).ai_title_label

        def work(conn, progress, should_stop):
            return services.caption_files(
                conn,
                model,
                limit=services.DEFAULT_CAPTION_LIMIT,
                per_call=services.DEFAULT_IMAGES_PER_CALL,
                batch_size=batch_size,
                names=targets,
                redo=redo,
                ai_label=ai_label,
                on_progress=progress,
                should_stop=should_stop,
            )

        self.start(work, self._caption_done)

    def _described_among(self, names: list[str] | None) -> int:
        """How many of the files already have a description that captioning would replace."""
        wanted = set(names or ())
        return sum(
            1 for row in self._rows if row["filename"] in wanted and row["status"] != state.NEEDS_METADATA
        )

    def _confirm_overwrite(self, count: int) -> bool:
        answer = QMessageBox.question(
            self,
            "Подписать заново?",
            f"У файлов с описанием: {count}. Подписать их заново и заменить "
            "заголовок, ключевые слова и категорию?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _caption_done(self, report: object) -> None:
        if not isinstance(report, services.CaptionReport):
            return
        if report.total == 0:
            self.write("Нечего подписывать: среди выбранных нет файлов, которые можно подписать.")
            self.status.setText("Нечего подписывать")
            return
        self.write(
            f"Подписано: {report.captioned} из {report.total}\nНа проверке: {len(report.flagged)}\n"
            f"Новых батчей: {report.new_batches}" + ("\nОтменено пользователем" if report.stopped else "")
        )
        for message in report.errors:
            self.write(f"ОШИБКА {message}")
        summary = "Готово" if not report.errors else "Готово, есть ошибки"
        if report.flagged:
            summary += f" · на проверке: {len(report.flagged)}"
        self.status.setText(summary)

    def _approve(self, names: list[str] | None) -> None:
        batch_size = self.ctx.prefs.batch_size

        def work(conn, progress, should_stop):
            return services.approve(conn, names, batch_size)

        self.start(work, self._approve_done)

    def _approve_done(self, report: object) -> None:
        if isinstance(report, services.ApproveReport):
            self.output.setVisible(False)  # the result is one line: the status line says it
            self.status.setText(f"Одобрено: {report.count}")
