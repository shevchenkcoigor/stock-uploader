"""History of what the app did (the `events` table)."""

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from adobe_stock import state
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.widgets import make_combo

COLUMNS = ("Время", "Уровень", "Команда", "Файл", "Сообщение")
LEVELS = (("Все", None), ("Только ошибки", "error"), ("Предупреждения", "warning"), ("Инфо", "info"))
EVENTS_SHOWN = 300


class LogPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        title = QLabel("Журнал")
        title.setProperty("role", "title")
        layout.addWidget(title)
        bar = QHBoxLayout()
        self.level = make_combo()
        for label, value in LEVELS:
            self.level.addItem(label, value)
        self.level.currentIndexChanged.connect(self.refresh)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.refresh)
        bar.addWidget(self.level)
        bar.addWidget(refresh)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(3, 200)
        layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        level = self.level.currentData()
        rows = self.ctx.read(lambda conn: state.recent_events(conn, EVENTS_SHOWN, None, level))
        self.table.setRowCount(len(rows))
        for index, row in enumerate(reversed(rows)):  # newest first in the window
            values = (row["at"], row["level"], row["command"], row["filename"] or "", row["message"])
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
