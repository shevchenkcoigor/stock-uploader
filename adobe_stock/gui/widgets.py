"""Small shared widgets."""

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase, QResizeEvent
from PySide6.QtWidgets import QComboBox, QLabel, QLayout, QListView, QSizePolicy, QWidget

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
        metrics = self.fontMetrics()  # what the label adds to its text (padding, margins) stays included
        shown = QLabel.text(self)
        wanted = hint.width() - metrics.horizontalAdvance(shown) + metrics.horizontalAdvance(self._full)
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


class ScrollBody(QWidget):
    """The content of a scroll area whose height depends on its width (wrapping tag chips).

    QScrollArea does not hold such content at its height-for-width minimum, so the fields got squeezed
    on top of each other in a short window. This keeps the minimum height in step with the width.
    """

    def event(self, event: QEvent) -> bool:  # noqa: N802
        handled = super().event(event)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            self._fit_height()
        return handled

    def fit_height(self) -> None:
        """Hold the minimum height this content needs at its current width."""
        self._fit_height()

    def _fit_height(self) -> None:
        layout = self.layout()
        if layout is None or self.width() <= 0:
            return
        needed = max(
            self._needed_height(layout, self.width()),
            layout.minimumHeightForWidth(self.width()),
            layout.sizeHint().height(),  # the preferred height leaves room for a few rows of tags
        )
        if needed > 0 and needed != self.minimumHeight():
            self.setMinimumHeight(needed)

    @staticmethod
    def _needed_height(layout: QLayout, width: int) -> int:
        """Sum of the rows' minimum heights at this width (Qt's own total under-counts wrapped rows)."""
        margins = layout.contentsMargins()
        inner = width - margins.left() - margins.right()
        heights = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None or item.isEmpty():
                continue
            wrapped = item.hasHeightForWidth()
            heights.append(
                max(item.minimumHeightForWidth(inner), item.heightForWidth(inner))
                if wrapped
                else item.minimumSize().height()
            )
        spacing = max(layout.spacing(), 0)
        return sum(heights) + spacing * max(len(heights) - 1, 0) + margins.top() + margins.bottom()


def mono_font() -> QFont:
    """The fixed-width font for file names and paths."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPixelSize(MONO_PIXELS)
    return font
