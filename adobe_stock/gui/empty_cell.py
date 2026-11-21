"""A table cell with nothing in it shows a muted hint and a red underline, so gaps stand out."""

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QWidget

from adobe_stock.gui.theme import active_colors

CELL_PADDING = 8
UNDERLINE_WIDTH = 2
UNDERLINE_OFFSET = 11  # below the middle of the cell, like a text underline
BOTTOM_GAP = 3  # keeps the line inside short rows


class EmptyCellDelegate(QStyledItemDelegate):
    """Plain cells (still editable as usual) that flag themselves when empty."""

    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._placeholder = placeholder

    def paint(  # noqa: N802
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().paint(painter, option, index)
        if index.data(Qt.ItemDataRole.DisplayRole):
            return
        colors = active_colors()
        area = option.rect.adjusted(CELL_PADDING, 0, -CELL_PADDING, 0)  # type: ignore[attr-defined]
        painter.save()
        painter.setPen(QColor(colors["ink-muted"]))
        hint = painter.fontMetrics().elidedText(self._placeholder, Qt.TextElideMode.ElideRight, area.width())
        painter.drawText(area, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), hint)
        y = min(area.center().y() + UNDERLINE_OFFSET, area.bottom() - BOTTOM_GAP)
        painter.setPen(QPen(QColor(colors["danger"]), UNDERLINE_WIDTH))
        painter.drawLine(area.left(), y, area.right(), y)
        painter.restore()
