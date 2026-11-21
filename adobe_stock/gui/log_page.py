"""History of what the app did (the `events` table): search, filters, sorting and live following."""

import sqlite3
from datetime import date, datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from adobe_stock import state
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.header_sort import HeaderSort
from adobe_stock.gui.theme import active_colors
from adobe_stock.gui.widgets import make_combo, mono_font

COLUMNS = ("Время", "Уровень", "Раздел", "Сообщение")
SORT_FIELDS = ("time", "level", "command", None)
LEVELS = (("Все уровни", None), ("Ошибки", "error"), ("Предупреждения", "warning"), ("Инфо", "info"))
ALL_SECTIONS = "Все разделы"
EVENTS_SHOWN = 300
FOLLOW_INTERVAL_MS = 2000
LEVEL_RANK = {"error": 0, "warning": 1, "info": 2}
LEVEL_COLOR = {"error": "danger", "warning": "warning", "info": "ink-muted"}
TIME_WIDTH, LEVEL_WIDTH, SECTION_WIDTH = 110, 120, 110
TABLE_ROW_HEIGHT = 34


def local_time(at: str) -> datetime:
    """The stored UTC timestamp as local time."""
    return datetime.fromisoformat(at).astimezone()


def message_text(row: sqlite3.Row) -> str:
    return f"{row['filename']}: {row['message']}" if row["filename"] else row["message"]


def sort_rows(rows: list[sqlite3.Row], field: str, descending: bool) -> list[sqlite3.Row]:
    """The rows ordered by a column; equal values keep the time order."""
    by_time = sorted(rows, key=lambda row: row["id"])
    keys = {
        "time": lambda row: row["id"],
        "level": lambda row: LEVEL_RANK.get(row["level"], len(LEVEL_RANK)),
        "command": lambda row: row["command"],
    }
    return sorted(by_time, key=keys[field], reverse=descending)


def day_label(newest: datetime | None, today: date) -> str:
    if newest is None:
        return ""
    return "сегодня" if newest.date() == today else newest.strftime("%d.%m.%Y")


def spaced(number: int) -> str:
    return f"{number:,}".replace(",", " ")


def level_badge(level: str) -> QLabel:
    colors = active_colors()
    dot = colors[LEVEL_COLOR.get(level, "ink-muted")]
    badge = QLabel(f'<span style="color:{dot}">●</span> {level.upper()}')
    badge.setProperty("role", "chip")
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return badge


class LogPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._after_id = 0  # «Очистить» hides what is already there; the history itself is kept
        self._rows: list[sqlite3.Row] = []
        self._total = 0
        self._follow = QTimer(self)
        self._follow.setInterval(FOLLOW_INTERVAL_MS)
        self._follow.timeout.connect(self.refresh)

        outer = QVBoxLayout(self)
        panel = QFrame()
        panel.setObjectName("panel")
        outer.addWidget(panel)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addLayout(self._head())
        column.addWidget(self._table(), 1)
        column.addLayout(self._foot())
        self.sorter = HeaderSort(
            self.table, COLUMNS, SORT_FIELDS, field="time", descending=False, on_change=self._fill
        )

    def _head(self) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setContentsMargins(20, 16, 20, 14)
        title = QLabel("Журнал")
        title.setProperty("role", "heading")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по тексту")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(210)
        self.search.textChanged.connect(self.refresh)
        self.level = make_combo()
        for label, value in LEVELS:
            self.level.addItem(label, value)
        self.level.currentIndexChanged.connect(self.refresh)
        self.section = make_combo()
        self.section.addItem(ALL_SECTIONS, None)
        self.section.currentIndexChanged.connect(self.refresh)
        self.copy_button = QPushButton("Копировать")
        self.copy_button.clicked.connect(self.copy_shown)
        self.clear_button = QPushButton("Очистить")
        self.clear_button.clicked.connect(self.clear_shown)
        head.addWidget(title)
        head.addStretch(1)
        for widget in (self.search, self.level, self.section, self.copy_button, self.clear_button):
            head.addWidget(widget)
        return head

    def _table(self) -> QTableWidget:
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for column, width in enumerate((TIME_WIDTH, LEVEL_WIDTH, SECTION_WIDTH)):
            self.table.setColumnWidth(column, width)
        return self.table

    def _foot(self) -> QHBoxLayout:
        foot = QHBoxLayout()
        foot.setContentsMargins(20, 12, 20, 14)
        self.count = QLabel("")
        self.count.setProperty("role", "muted")
        self.follow_button = QPushButton("Следить")
        self.follow_button.setCheckable(True)
        self.follow_button.setProperty("segment", True)
        self.follow_button.toggled.connect(self._toggle_follow)
        foot.addWidget(self.count)
        foot.addStretch(1)
        foot.addWidget(self.follow_button)
        return foot

    # ------------------------------------------------------------------ data

    def refresh(self) -> None:
        text, level, section = (
            self.search.text().strip(),
            self.level.currentData(),
            self.section.currentData(),
        )

        def query(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], int, list[str]]:
            rows, total = state.search_events(
                conn, EVENTS_SHOWN, text=text, level=level, command=section, after_id=self._after_id
            )
            return rows, total, state.event_commands(conn)

        self._rows, self._total, commands = self.ctx.read(query)
        self._fill_sections(commands)
        self._fill()

    def _fill_sections(self, commands: list[str]) -> None:
        current = self.section.currentData()
        self.section.blockSignals(True)
        self.section.clear()
        self.section.addItem(ALL_SECTIONS, None)
        for command in commands:
            self.section.addItem(command, command)
        self.section.setCurrentIndex(max(self.section.findData(current), 0))
        self.section.blockSignals(False)

    def _fill(self) -> None:
        rows = sort_rows(self._rows, self.sorter.field, self.sorter.descending)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self.table.setItem(index, 0, self._cell(local_time(row["at"]).strftime("%H:%M:%S"), mono=True))
            self.table.setCellWidget(index, 1, self._badge_cell(row["level"]))
            self.table.setItem(index, 2, self._cell(row["command"], mono=True))
            self.table.setItem(index, 3, self._cell(message_text(row)))
        newest = local_time(self._rows[0]["at"]) if self._rows else None
        day = day_label(newest, date.today())
        shown = f"Показано {len(rows)} из {spaced(self._total)}"
        self.count.setText(f"{shown} · {day}" if day else shown)
        if self.follow_button.isChecked():
            self.table.scrollToBottom()

    @staticmethod
    def _cell(text: str, mono: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if mono:
            item.setFont(mono_font())
        return item

    @staticmethod
    def _badge_cell(level: str) -> QWidget:
        cell = QWidget()
        inside = QHBoxLayout(cell)
        inside.setContentsMargins(8, 4, 8, 4)
        inside.addWidget(level_badge(level))
        inside.addStretch(1)
        return cell

    # ------------------------------------------------------------------ actions

    def copy_shown(self) -> None:
        """The shown lines as tab-separated text."""
        rows = sort_rows(self._rows, self.sorter.field, self.sorter.descending)
        lines = [
            "\t".join(
                (local_time(row["at"]).strftime("%H:%M:%S"), row["level"], row["command"], message_text(row))
            )
            for row in rows
        ]
        QGuiApplication.clipboard().setText("\n".join(lines))

    def clear_shown(self) -> None:
        """Hide everything logged so far (the history in the database stays)."""
        self._after_id = self.ctx.read(state.last_event_id)
        self.refresh()

    def _toggle_follow(self, on: bool) -> None:
        if on:
            self._follow.start()
            self.refresh()
        else:
            self._follow.stop()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.follow_button.setChecked(False)  # no polling for a page nobody looks at
        super().hideEvent(event)
