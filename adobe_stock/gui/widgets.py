"""Small shared widgets."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase, QResizeEvent
from PySide6.QtWidgets import QComboBox, QLabel, QListView, QSizePolicy, QWidget

MONO_PIXELS = 12


def make_combo() -> QComboBox:
    """A drop-down whose list can be styled like the rest of the app (rows, rounded popup)."""
    combo = QComboBox()
    combo.setView(QListView())
    return combo


class ElidedLabel(QLabel):
    """A one-line label that never asks for more room than it is given: long text is cut with "…".

    A plain QLabel's minimum width is its whole text, so a long file name or tag used to push the
    window wider. text() still returns the full text.
    """

    def __init__(self, text: str = "", max_width: int | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self._max_width = max_width
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored if max_width is None else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        self.setText(text)

    def text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:  # noqa: N802 (Qt override)
        self._full = text
        self._elide()
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        wanted = self.fontMetrics().horizontalAdvance(self._full) + 2 * self.margin() + self.indent()
        if self._max_width is not None:
            wanted = min(wanted, self._max_width)
        return QSize(wanted, hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        width = self.width() if self.isVisible() or self.width() > 1 else self.sizeHint().width()
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(width, 1))
        super().setText(shown)


def mono_font() -> QFont:
    """The fixed-width font for file names and paths."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPixelSize(MONO_PIXELS)
    return font
