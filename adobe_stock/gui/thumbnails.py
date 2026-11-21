"""Fast scaled previews of images."""

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImageReader, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from adobe_stock.gui.theme import TOKENS, active_theme


def thumbnail_pixmap(path: str, side: int) -> QPixmap:
    """Scaled decode, so even a 100 MP JPEG does not freeze the window. Empty pixmap when unreadable."""
    reader = QImageReader(path)
    size = reader.size()
    if size.isValid():
        size.scale(side, side, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    image = reader.read()
    return QPixmap() if image.isNull() else QPixmap.fromImage(image)


# ---- the design's ThumbnailTile: a framed square preview, 40px in lists --------------------------

DEVICE_SCALE = 2  # drawn at twice the size so it stays sharp on retina screens
TILE_RADIUS = 4
BORDER_WIDTH = 1
DOT_DIAMETER = 10  # the warning dot of a file held for review
DOT_MARGIN = 3
GLYPH_SHARE = 0.44  # the placeholder glyph covers this part of the tile
GLYPH_STROKE = 1.5
CACHE_SIZE = 512
RESOLUTION_CACHE = 50_000


@lru_cache(maxsize=CACHE_SIZE)
def tile_pixmap(path: str, side: int, badge: str, theme: str) -> QPixmap:
    """The preview on a sunken backing with a border; a glyph when the picture cannot be read."""
    colors = TOKENS[theme]
    pixels = side * DEVICE_SCALE
    tile = QPixmap(pixels, pixels)
    tile.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tile)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = TILE_RADIUS * DEVICE_SCALE
    frame = QRectF(0.5, 0.5, pixels - 1, pixels - 1)
    clip = QPainterPath()
    clip.addRoundedRect(frame, radius, radius)
    painter.setClipPath(clip)
    painter.fillPath(clip, QColor(colors["surface-sunken"]))
    picture = thumbnail_pixmap(path, pixels) if path else QPixmap()
    if picture.isNull():
        _draw_glyph(painter, pixels, QColor(colors["ink-muted"]))
    else:  # fitted whole, never cropped
        painter.drawPixmap((pixels - picture.width()) // 2, (pixels - picture.height()) // 2, picture)
    painter.setClipping(False)
    painter.setPen(QPen(QColor(colors["border"]), BORDER_WIDTH * DEVICE_SCALE))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(frame, radius, radius)
    if badge:
        _draw_dot(painter, pixels, colors, badge)
    painter.end()
    tile.setDevicePixelRatio(DEVICE_SCALE)
    return tile


def _draw_glyph(painter: QPainter, pixels: int, color: QColor) -> None:
    """An outline 'image' icon: frame, sun and a mountain line."""
    size = pixels * GLYPH_SHARE
    left = top = (pixels - size) / 2
    painter.setPen(QPen(color, GLYPH_STROKE * DEVICE_SCALE, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(left, top, size, size), size * 0.12, size * 0.12)
    painter.drawEllipse(QPointF(left + size * 0.68, top + size * 0.32), size * 0.08, size * 0.08)
    mountain = QPolygonF(
        [
            QPointF(left, top + size * 0.82),
            QPointF(left + size * 0.38, top + size * 0.48),
            QPointF(left + size, top + size * 0.88),
        ]
    )
    painter.drawPolyline(mountain)


def _draw_dot(painter: QPainter, pixels: int, colors, token: str) -> None:
    diameter = DOT_DIAMETER * DEVICE_SCALE
    margin = DOT_MARGIN * DEVICE_SCALE
    painter.setPen(
        QPen(QColor(colors["surface"]), 2 * DEVICE_SCALE)
    )  # the border keeps it readable on any picture
    painter.setBrush(QColor(colors[token]))
    painter.drawEllipse(QRectF(pixels - diameter - margin, margin, diameter, diameter))


@lru_cache(maxsize=RESOLUTION_CACHE)
def image_size(path: str) -> tuple[int, int]:
    """Width and height in pixels from the file header (no decoding); (0, 0) when unreadable."""
    size = QImageReader(path).size()
    return (size.width(), size.height()) if size.isValid() else (0, 0)


def tile_icon(path: str, side: int, badge: str = "") -> QIcon:
    """`badge` is a colour token (e.g. "warning"): a dot of that colour marks the tile's corner."""
    return QIcon(tile_pixmap(path, side, badge, active_theme()))
