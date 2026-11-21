"""File statuses as the design shows them: a word plus a coloured dot, problems first."""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTableWidget, QWidget

from adobe_stock import state
from adobe_stock.gui.theme import active_colors

DOT_SIDE = 10
CHIP_HEIGHT = 24


@dataclass(frozen=True)
class StatusView:
    label: str
    token: str  # colour token of the dot
    rank: int  # sort order: lower = needs attention sooner


STATUS_VIEWS = {
    state.INVALID: StatusView("Ошибка", "danger", 0),
    state.REVIEW: StatusView("Проверить", "warning", 1),
    state.NEEDS_METADATA: StatusView("Новый", "ink-muted", 2),
    state.PENDING: StatusView("Описан", "accent", 3),
    state.UPLOADED: StatusView("Загружен", "success", 4),
    state.SUBMITTED: StatusView("Отправлен", "success", 5),
}
UNKNOWN_VIEW = StatusView("—", "ink-muted", 99)


def view_of(status: str) -> StatusView:
    return STATUS_VIEWS.get(status, UNKNOWN_VIEW)


def dot_icon(token: str) -> QIcon:
    pixmap = QPixmap(DOT_SIDE, DOT_SIDE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(active_colors()[token]))
    painter.drawEllipse(0, 0, DOT_SIDE, DOT_SIDE)
    painter.end()
    return QIcon(pixmap)


def chip_html(status: str) -> str:
    view = view_of(status)
    return f'<span style="color:{active_colors()[view.token]}">●</span> {view.label}'


def chip(status: str) -> QLabel:
    """A pill with a coloured dot and the status word, for table cells."""
    label = QLabel(chip_html(status))
    label.setProperty("role", "chip")
    label.setFixedHeight(CHIP_HEIGHT)  # a pill of its own height, however tall the row is
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


def status_cell(status: str) -> QWidget:
    """A table cell with the status chip on the left."""
    holder = QWidget()
    inner = QHBoxLayout(holder)
    inner.setContentsMargins(8, 4, 8, 4)
    inner.addWidget(chip(status), 0, Qt.AlignmentFlag.AlignVCenter)  # not stretched over a tall row
    inner.addStretch(1)
    return holder


def drop_cell_widgets(table: QTableWidget, column: int) -> None:
    """Remove the chips of a column right now (not at some later event loop turn) before refilling."""
    for row in range(table.rowCount()):
        old = table.cellWidget(row, column)
        if old is not None:
            old.hide()
            old.deleteLater()
    table.clearContents()
