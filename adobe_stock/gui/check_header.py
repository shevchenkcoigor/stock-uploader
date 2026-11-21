"""A table header with a "mark all" checkbox at the left of the first column."""

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHeaderView, QTableWidget, QWidget

from adobe_stock.gui.theme import active_colors

BOX_SIDE = 16
BOX_MARGIN = 10  # from the section's left edge; keep in step with the header's padding in the theme
BOX_RADIUS = 4


class CheckHeader(QHeaderView):
    """Draws a checkbox (unchecked / partly / all) in section 0; a click on it never sorts."""

    toggled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._state = Qt.CheckState.Unchecked
        self._pressed_on_box = False

    def set_state(self, state: Qt.CheckState) -> None:
        if state != self._state:
            self._state = state
            self.viewport().update()

    def check_state(self) -> Qt.CheckState:
        return self._state

    def box_rect(self) -> QRect:
        left = self.sectionViewportPosition(0) + BOX_MARGIN
        return QRect(left, (self.height() - BOX_SIDE) // 2, BOX_SIDE, BOX_SIDE)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:  # noqa: N802
        super().paintSection(painter, rect, logicalIndex)
        if logicalIndex != 0:
            return
        colors = active_colors()
        box = QRectF(self.box_rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        marked = self._state != Qt.CheckState.Unchecked
        painter.setPen(QPen(QColor(colors["accent" if marked else "border-strong"]), 1))
        painter.setBrush(QColor(colors["accent" if marked else "surface"]))
        painter.drawRoundedRect(box, BOX_RADIUS, BOX_RADIUS)
        if marked:
            painter.setPen(
                QPen(QColor(colors["accent-ink"]), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            middle = box.center()
            if self._state == Qt.CheckState.PartiallyChecked:
                painter.drawLine(
                    QPoint(int(box.left() + 4), int(middle.y())),
                    QPoint(int(box.right() - 4), int(middle.y())),
                )
            else:
                painter.drawPolyline(
                    [
                        QPoint(int(box.left() + 4), int(middle.y())),
                        QPoint(int(box.left() + 7), int(box.bottom() - 4)),
                        QPoint(int(box.right() - 3), int(box.top() + 4)),
                    ]
                )
        painter.restore()

    def _on_box(self, event: QMouseEvent) -> bool:
        return self.box_rect().adjusted(-4, -4, 4, 4).contains(event.position().toPoint())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._pressed_on_box = event.button() == Qt.MouseButton.LeftButton and self._on_box(event)
        if not self._pressed_on_box:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._pressed_on_box:
            self._pressed_on_box = False
            if self._on_box(event):
                self.toggled.emit()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._on_box(event):
            super().mouseDoubleClickEvent(event)


def install_check_header(table: QTableWidget) -> CheckHeader:
    header = CheckHeader(table)
    table.setHorizontalHeader(header)
    header.setProperty("checkable", True)
    return header
