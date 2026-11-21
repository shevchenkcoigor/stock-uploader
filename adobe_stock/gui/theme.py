# ruff: noqa: E501
"""Visual theme from the design system (design - Adobe Stock Uploader/tokens.css): palette + stylesheet."""

import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEME_KEY = "theme"
THEME_PROPERTY = "active_theme"
THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_CHOICES: Final = (
    ("Как в системе", THEME_SYSTEM),
    ("Светлая", THEME_LIGHT),
    ("Тёмная", THEME_DARK),
)

TOKENS = MappingProxyType(
    {
        THEME_LIGHT: MappingProxyType(
            {
                "window": "#f3f4f6",
                "surface": "#ffffff",
                "surface-sunken": "#eceef2",
                "border": "#d0d4dc",
                "border-strong": "#8a92a2",
                "ink": "#1f2430",
                "ink-muted": "#5a6272",
                "accent": "#2563eb",
                "accent-ink": "#ffffff",
                "accent-soft": "#dbe7ff",
                "success": "#15803d",
                "info": "#0e7490",
                "warning": "#b45309",
                "danger": "#b91c1c",
            }
        ),
        THEME_DARK: MappingProxyType(
            {
                "window": "#1e2025",
                "surface": "#272a31",
                "surface-sunken": "#181a1f",
                "border": "#3a3e48",
                "border-strong": "#7a8397",
                "ink": "#e8eaf0",
                "ink-muted": "#b1b7c4",
                "accent": "#6ea0ff",
                "accent-ink": "#0b1220",
                "accent-soft": "#2d3f73",
                "success": "#4ade80",
                "info": "#22d3ee",
                "warning": "#fbbf24",
                "danger": "#f87171",
            }
        ),
    }
)

STYLESHEET = """
* {{ font-size: 13px; }}
QMainWindow, QStackedWidget#pages > QWidget {{ background: {window}; color: {ink}; }}
QLabel {{ color: {ink}; background: transparent; }}
QLabel[role="title"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {ink_muted}; font-size: 12px; }}
QLabel[role="path"] {{ font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; }}
QLabel[role="warn"] {{ color: {warning}; font-size: 12px; }}
QLabel[role="preview"] {{ background: {surface_sunken}; border: 1px solid {border}; border-radius: 4px; color: {ink_muted}; }}
QFrame#panel {{ background: {surface}; border: 1px solid {border}; border-radius: 6px; }}
QFrame#panel QLabel {{ background: transparent; }}
QPushButton[segment="true"] {{ height: 28px; min-width: 56px; padding: 0 8px; border-radius: 4px; }}
QPushButton[joint="first"] {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
QPushButton[joint="mid"] {{ border-radius: 0; }}
QPushButton[joint="last"] {{ border-top-left-radius: 0; border-bottom-left-radius: 0; }}
QPushButton[compact="true"] {{ min-width: 32px; }}
QPushButton[page="true"] {{ height: 28px; min-width: 28px; padding: 0 6px; }}
QListWidget#tiles {{ background: {surface}; border: 0; }}
QListWidget#tiles::item {{ border-radius: 4px; color: {ink}; }}
QScrollArea#card-scroll, QWidget#card-body {{ background: transparent; }}
QFrame#bulk {{ background: {accent_soft}; border-radius: 8px; }}
QFrame#bulk QLabel {{ background: transparent; }}
QListWidget#tiles::item:selected {{ background: {accent_soft}; color: {ink}; }}
QFrame#tile {{ background: {surface}; border: 1px solid {border}; border-radius: 6px; }}
QFrame#tile QLabel {{ background: transparent; }}
QLabel[role="stat"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="stat-danger"] {{ font-size: 20px; font-weight: 600; color: {danger}; }}
QLabel[role="chip"], QFrame#panel QLabel[role="chip"] {{ border: 1px solid {border}; border-radius: 11px; background: {surface_sunken}; padding: 1px 10px; font-size: 12px; }}
QLabel[role="error"] {{ color: {danger}; font-size: 12px; }}
QLineEdit[mono="true"] {{ font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; }}
QHeaderView[checkable="true"]::section:first {{ padding-left: 36px; }}
QCheckBox {{ spacing: 8px; color: {ink}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {border_strong}; border-radius: 4px; background: {surface}; }}
QTableView::indicator, QListView::indicator {{ width: 16px; height: 16px; border: 1px solid {border_strong}; border-radius: 4px; background: {surface}; }}
QTableView::indicator:checked, QListView::indicator:checked {{ background: {accent}; border-color: {accent}; image: url("{tick_url}"); }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url("{tick_url}"); }}
QCheckBox::indicator:focus {{ border: 2px solid {accent}; }}
QToolTip {{ background: {surface}; color: {ink}; border: 1px solid {border}; }}

QWidget#sidebar {{ background: {window}; border-right: 1px solid {border}; }}
QListWidget#nav {{ background: {window}; border: 0; padding: 12px 8px; outline: 0; }}
QListWidget#nav::item {{ height: 30px; padding: 0 12px; border-radius: 6px; color: {ink}; }}
QListWidget#nav::item:selected {{ background: {accent}; color: {accent_ink}; font-weight: 600; }}
QListWidget#nav::item:hover:!selected {{ background: {surface_sunken}; }}

QPushButton {{ height: 30px; padding: 0 12px; border: 1px solid {accent}; border-radius: 6px;
    background: {accent}; color: {accent_ink}; font-weight: 600; }}
QPushButton:hover {{ border-color: {ink}; }}
QPushButton:pressed {{ background: {accent_soft}; border-color: {accent_soft}; color: {ink}; }}
QPushButton:focus {{ border: 2px solid {ink}; }}
QPushButton:disabled {{ background: {accent_soft}; border-color: {accent_soft}; color: {ink_muted}; }}
QPushButton[segment="true"], QPushButton[page="true"] {{ background: {surface}; border-color: {border_strong};
    color: {ink}; font-weight: 400; }}
QPushButton[segment="true"]:hover, QPushButton[page="true"]:hover {{ background: {accent_soft}; }}
QPushButton[segment="true"]:disabled, QPushButton[page="true"]:disabled {{ background: {surface};
    border-color: {border}; color: {ink_muted}; }}
QPushButton[segment="true"]:checked, QPushButton[page="true"]:checked {{ background: {accent_soft};
    border-color: {accent}; font-weight: 600; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{ height: 30px; padding: 0 8px; border: 1px solid {border_strong};
    border-radius: 4px; background: {surface}; color: {ink}; selection-background-color: {accent};
    selection-color: {accent_ink}; }}
QPlainTextEdit, QTextEdit {{ height: auto; padding: 8px; font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    font-size: 12px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 2px solid {accent}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: 0; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{chevron_url}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 8px; border-radius: 4px; }}
QComboBox QAbstractItemView::item:hover {{ background: {accent_soft}; color: {ink}; }}
QFrame#tag {{ background: {surface}; border: 1px solid {border_strong}; border-radius: 12px; min-height: 24px; max-height: 24px; }}
QFrame#tag QLabel {{ background: transparent; font-size: 12px; }}
QToolButton#tag-remove {{ border: 0; background: transparent; color: {ink_muted}; font-size: 14px; padding: 0 4px; }}
QToolButton#tag-remove:hover {{ color: {danger}; }}
QLineEdit#tag-edit {{ height: 20px; min-height: 20px; border: 1px solid {accent}; padding: 0 4px; font-size: 12px; }}
QComboBox QAbstractItemView {{ background: {surface}; color: {ink}; border: 1px solid {border_strong}; border-radius: 6px; padding: 4px; outline: 0;
    selection-background-color: {accent_soft}; selection-color: {ink}; }}

QTableView, QTableWidget {{ background: {surface}; alternate-background-color: {surface}; border: 1px solid {border};
    border-radius: 6px; gridline-color: {border}; selection-background-color: {accent_soft}; selection-color: {ink}; }}
QHeaderView::section {{ padding: 6px 12px; background: {surface_sunken}; border: 0;
    border-bottom: 1px solid {border}; font-weight: 600; font-size: 12px; }}

QProgressBar {{ height: 8px; border: 1px solid {border}; border-radius: 4px; background: {surface_sunken};
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QStatusBar {{ background: {window}; color: {ink_muted}; border-top: 1px solid {border}; font-size: 12px; }}
QStatusBar QLabel {{ color: {ink_muted}; }}
QScrollBar:vertical {{ width: 12px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {border_strong}; border-radius: 4px; min-height: 24px; margin: 2px; }}
QScrollBar:horizontal {{ height: 12px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {border_strong}; border-radius: 4px; min-width: 24px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
"""

PALETTE_ROLES = (
    (QPalette.ColorRole.Window, "window"),
    (QPalette.ColorRole.Base, "surface"),
    (QPalette.ColorRole.AlternateBase, "surface-sunken"),
    (QPalette.ColorRole.Button, "surface"),
    (QPalette.ColorRole.WindowText, "ink"),
    (QPalette.ColorRole.Text, "ink"),
    (QPalette.ColorRole.ButtonText, "ink"),
    (QPalette.ColorRole.PlaceholderText, "ink-muted"),
    (QPalette.ColorRole.Highlight, "accent"),
    (QPalette.ColorRole.HighlightedText, "accent-ink"),
)


def resolve_theme(choice: str, system_is_dark: bool) -> str:
    """The concrete theme (light or dark) for a saved choice."""
    if choice in (THEME_LIGHT, THEME_DARK):
        return choice
    return THEME_DARK if system_is_dark else THEME_LIGHT


def build_palette(theme: str) -> QPalette:
    colors = TOKENS[theme]
    palette = QPalette()
    for role, token in PALETTE_ROLES:
        palette.setColor(role, QColor(colors[token]))
    return palette


def _svg_file(name: str, theme: str, shape: str) -> Path:
    """An icon as a file, since QSS can only point to files (drawn in the theme's colours)."""
    path = Path(tempfile.gettempdir()) / f"adobe-stock-uploader-{name}-{theme}.svg"
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">{shape}</svg>'
    if not path.exists() or path.read_text(encoding="utf-8") != svg:
        path.write_text(svg, encoding="utf-8")
    return path


def tick_file(theme: str) -> Path:
    """The check mark of a ticked box."""
    ink = TOKENS[theme]["accent-ink"]
    shape = (
        f'<path d="M4 8.5l2.8 2.8L12 5.5" fill="none" stroke="{ink}" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    return _svg_file("tick", theme, shape)


def chevron_file(theme: str) -> Path:
    """The little arrow of a drop-down."""
    ink = TOKENS[theme]["ink-muted"]
    shape = (
        f'<path d="M4 6.5l4 4 4-4" fill="none" stroke="{ink}" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    return _svg_file("chevron", theme, shape)


def build_stylesheet(theme: str) -> str:
    colors = {name.replace("-", "_"): value for name, value in TOKENS[theme].items()}
    return STYLESHEET.format(
        **colors, tick_url=tick_file(theme).as_posix(), chevron_url=chevron_file(theme).as_posix()
    )


def system_is_dark(app: QApplication) -> bool:
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply_theme(app: QApplication, choice: str) -> str:
    """Apply the saved choice to the whole app; returns the concrete theme used."""
    theme = resolve_theme(choice, system_is_dark(app))
    app.setStyle("Fusion")
    app.setPalette(build_palette(theme))
    app.setStyleSheet(build_stylesheet(theme))
    app.setProperty(THEME_PROPERTY, theme)
    return theme


def active_theme() -> str:
    """Name of the theme applied last (light before any theme is applied)."""
    app = QApplication.instance()
    name = app.property(THEME_PROPERTY) if app is not None else None
    return str(name) if name in TOKENS else THEME_LIGHT


def active_colors() -> "MappingProxyType[str, str]":
    return TOKENS[active_theme()]
