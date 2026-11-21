"""Line icons for the list / tiles toggle, drawn in the theme's colours (idle and checked)."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from adobe_stock.gui.theme import active_colors

ICON_SIDE = 16
STROKE = 1.6
DEVICE_SCALE = 2


def _canvas() -> QPixmap:
    pixmap = QPixmap(ICON_SIDE * DEVICE_SCALE, ICON_SIDE * DEVICE_SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(DEVICE_SCALE)
    return pixmap


def _pen(color: str) -> QPen:
    return QPen(QColor(color), STROKE, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)


def _list_pixmap(color: str) -> QPixmap:
    pixmap = _canvas()
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(_pen(color))
    for y in (3.5, 8, 12.5):
        painter.drawLine(QPointF(2, y), QPointF(14, y))
    painter.end()
    return pixmap


def _grid_pixmap(color: str) -> QPixmap:
    pixmap = _canvas()
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for x in (2.5, 9):
        for y in (2.5, 9):
            painter.drawRoundedRect(QRectF(x, y, 4.5, 4.5), 1, 1)
    painter.end()
    return pixmap


def _icon(draw) -> QIcon:
    colors = active_colors()
    icon = QIcon()
    icon.addPixmap(draw(colors["ink"]), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(draw(colors["accent"]), QIcon.Mode.Normal, QIcon.State.On)
    return icon


def list_icon() -> QIcon:
    return _icon(_list_pixmap)


def grid_icon() -> QIcon:
    return _icon(_grid_pixmap)
