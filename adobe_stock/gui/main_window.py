"""Main window: a step list on the left, the current step on the right, a status line below."""

import json
from datetime import datetime
from typing import cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from adobe_stock import services, state
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.csv_page import CsvPage
from adobe_stock.gui.description_page import DescriptionPage
from adobe_stock.gui.log_page import LogPage
from adobe_stock.gui.prefs import APP_NAME
from adobe_stock.gui.scan_page import LAST_SCAN_KEY, ScanPage, last_scan_text
from adobe_stock.gui.settings_page import SettingsPage
from adobe_stock.gui.upload_page import UploadPage

SETTINGS_TITLE = "Настройки"
NAV_WIDTH = 180
NAV_ITEM_HEIGHT = 30
NAV_PADDING = 24

STATUS_LABELS = (
    (state.NEEDS_METADATA, "ждут подписи"),
    (state.REVIEW, "на проверке"),
    (state.PENDING, "к загрузке"),
    (state.UPLOADED, "загружено"),
    (state.SUBMITTED, "отправлено"),
    (state.INVALID, "невалидных"),
)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)

        self.pages: list[tuple[str, QWidget]] = [
            ("1. Скан", ScanPage(ctx)),
            ("2. Описание", DescriptionPage(ctx)),
            ("3. Загрузка", UploadPage(ctx)),
            ("4. CSV и отправка", CsvPage(ctx)),
            ("Журнал", LogPage(ctx)),
            ("Настройки", SettingsPage(ctx)),  # last: shown at the bottom of the navigation
        ]
        self.nav = self._nav_list()
        self.nav_bottom = self._nav_list()
        self.stack = QStackedWidget()
        self.stack.setObjectName("pages")
        for title, page in self.pages:
            (self.nav_bottom if title == SETTINGS_TITLE else self.nav).addItem(title)
            self.stack.addWidget(page)
        self.nav_bottom.setFixedHeight(NAV_ITEM_HEIGHT + NAV_PADDING)
        self.nav.currentRowChanged.connect(self._show_top)
        self.nav_bottom.currentRowChanged.connect(self._show_bottom)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(NAV_WIDTH)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        side_layout.addWidget(self.nav, 1)
        side_layout.addWidget(self.nav_bottom)

        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(body)

        self.summary = QLabel("")
        self.last_scan = QLabel("")
        self.statusBar().addWidget(self.summary, 1)
        self.statusBar().addPermanentWidget(self.last_scan)
        cast(ScanPage, self.pages[0][1]).to_description.clicked.connect(lambda: self.nav.setCurrentRow(1))
        ctx.data_changed.connect(self.refresh)
        self.nav.setCurrentRow(0)
        self.refresh()

    @staticmethod
    def _nav_list() -> QListWidget:
        nav = QListWidget()
        nav.setObjectName("nav")
        nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return nav

    def _show_top(self, row: int) -> None:
        if row < 0:
            return
        self.nav_bottom.blockSignals(True)
        self.nav_bottom.clearSelection()
        self.nav_bottom.setCurrentRow(-1)
        self.nav_bottom.blockSignals(False)
        self._show_page(row)

    def _show_bottom(self, row: int) -> None:
        if row < 0:
            return
        self.nav.blockSignals(True)
        self.nav.clearSelection()
        self.nav.setCurrentRow(-1)
        self.nav.blockSignals(False)
        self._show_page(len(self.pages) - 1)

    def _show_page(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        self._refresh_page(self.pages[row][1])

    @staticmethod
    def _refresh_page(page: QWidget) -> None:
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    def _saved_scan(self) -> dict:
        try:
            return json.loads(self.ctx.prefs.text(LAST_SCAN_KEY, "{}"))
        except ValueError:
            return {}

    def refresh(self) -> None:
        """Update the status line and the visible page after any change."""
        try:
            rows = self.ctx.read(state.status_summary)
        except services.StateDbError as error:
            self.summary.setText(str(error))
            return
        totals: dict[str, int] = {}
        for row in rows:
            totals[row["status"]] = totals.get(row["status"], 0) + row["n"]
        parts = [f"{label}: {totals[key]}" for key, label in STATUS_LABELS if totals.get(key)]
        self.summary.setText(" · ".join(parts) if parts else "База пуста: начните со скана")
        self.last_scan.setText(last_scan_text(self._saved_scan(), datetime.now()))
        current = self.stack.currentWidget()
        if current is not None:
            self._refresh_page(current)
