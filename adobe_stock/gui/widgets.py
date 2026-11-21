"""Small shared widgets."""

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QComboBox, QListView

MONO_PIXELS = 12


def make_combo() -> QComboBox:
    """A drop-down whose list can be styled like the rest of the app (rows, rounded popup)."""
    combo = QComboBox()
    combo.setView(QListView())
    return combo


def mono_font() -> QFont:
    """The fixed-width font for file names and paths."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPixelSize(MONO_PIXELS)
    return font
