"""Main window: a step list on the left, the current step on the right, a status line below."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from adobe_stock import services, state
from adobe_stock.gui.caption_page import CaptionPage
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.csv_page import CsvPage
from adobe_stock.gui.log_page import LogPage
from adobe_stock.gui.prefs import APP_NAME
from adobe_stock.gui.review_page import ReviewPage
from adobe_stock.gui.scan_page import ScanPage
from adobe_stock.gui.settings_page import SettingsPage
from adobe_stock.gui.upload_page import UploadPage

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
            ("Настройки", SettingsPage(ctx)),
            ("1. Скан", ScanPage(ctx)),
            ("2. Подписи", CaptionPage(ctx)),
            ("3. Проверка", ReviewPage(ctx)),
            ("4. Загрузка", UploadPage(ctx)),
            ("5. CSV и отправка", CsvPage(ctx)),
            ("Журнал", LogPage(ctx)),
        ]
        self.nav = QListWidget()
        self.nav.setFixedWidth(190)
        self.stack = QStackedWidget()
        for title, page in self.pages:
            self.nav.addItem(title)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self._show_page)

        body = QWidget()
        layout = QHBoxLayout(body)
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(body)

        self.summary = QLabel("")
        self.statusBar().addWidget(self.summary, 1)
        ctx.data_changed.connect(self.refresh)
        self.nav.setCurrentRow(1)
        self.refresh()

    def _show_page(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        self._refresh_page(self.pages[row][1])

    @staticmethod
    def _refresh_page(page: QWidget) -> None:
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

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
        current = self.stack.currentWidget()
        if current is not None:
            self._refresh_page(current)
