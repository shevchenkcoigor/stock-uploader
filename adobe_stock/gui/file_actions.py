"""Open a file from a table row, or show it in the file manager (context menu and double-click)."""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QMenu

FILE_PATH_ROLE = Qt.ItemDataRole.UserRole + 1  # the full path of the file a row or tile stands for


def open_file(path: str) -> bool:
    """Open the file in the system's default application; False when it is gone from the disk."""
    if not path or not Path(path).exists():
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal_file(path: str) -> bool:
    """Show the file selected in Finder / Explorer (the folder on other systems)."""
    if not path or not Path(path).exists():
        return False
    if sys.platform == "darwin":
        command = ["open", "-R", path]
    elif sys.platform == "win32":
        command = ["explorer", f"/select,{Path(path)}"]
    else:
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))
    subprocess.Popen(command)  # noqa: S603 - fixed argument list, no shell
    return True


def attach_file_menu(view: QAbstractItemView, path_at: Callable[[QPoint], str]) -> None:
    """Right-click on a row or tile offers to open the file or show it in the folder."""
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show(position: QPoint) -> None:
        path = path_at(position)
        if not path:
            return
        menu = QMenu(view)
        exists = Path(path).exists()
        menu.addAction("Открыть файл", lambda: open_file(path)).setEnabled(exists)
        menu.addAction("Показать в папке", lambda: reveal_file(path)).setEnabled(exists)
        menu.exec(view.viewport().mapToGlobal(position))

    view.customContextMenuRequested.connect(show)
