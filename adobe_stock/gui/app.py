"""Entry point of the desktop app."""

import sys
from typing import cast

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from adobe_stock.gui.context import AppContext
from adobe_stock.gui.main_window import MainWindow
from adobe_stock.gui.prefs import APP_NAME, KeychainStore, Preferences
from adobe_stock.gui.theme import THEME_KEY, THEME_SYSTEM, apply_theme

SMOKE_FLAG = "--smoke"
SMOKE_MILLISECONDS = 400


def build_window(prefs: Preferences) -> MainWindow:
    prefs.apply_environment()
    return MainWindow(AppContext(prefs))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(args)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    prefs = Preferences(QSettings(), KeychainStore())
    apply_theme(cast(QApplication, app), prefs.text(THEME_KEY, THEME_SYSTEM))
    window = build_window(prefs)
    window.show()
    if SMOKE_FLAG in args:  # used to check a freshly built package starts: open, then quit
        QTimer.singleShot(SMOKE_MILLISECONDS, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
