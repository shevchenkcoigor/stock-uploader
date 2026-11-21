"""Open a file from a table row, or show it in the file manager (context menu and double-click)."""

import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QMenu, QTableWidget

FILE_PATH_ROLE = Qt.ItemDataRole.UserRole + 1  # the full path of the file a row or tile stands for
CHECK_ZONE = 28  # px from the cell's left edge taken by a checkbox: a click there ticks, it does not open


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


def _on_check_box(table: QTableWidget, row: int, column: int) -> bool:
    """True when the click that just happened landed on the cell's checkbox."""
    item = table.item(row, column)
    if item is None or not item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
        return False
    cell = table.visualRect(table.indexFromItem(item))
    click = table.viewport().mapFromGlobal(QCursor.pos())
    return cell.contains(click) and click.x() - cell.left() < CHECK_ZONE


def open_on_click(
    table: QTableWidget,
    columns: Sequence[int] | None = None,
    on_missing: Callable[[str], None] | None = None,
) -> None:
    """A click on a picture or its name opens the file; `columns` limits it (all cells when None).

    The path is read from FILE_PATH_ROLE of the row's first cell, so a row needs only that one item.
    """

    def clicked(row: int, column: int) -> None:
        if columns is not None and column not in columns:
            return
        item = table.item(row, 0)
        path = str(item.data(FILE_PATH_ROLE) or "") if item is not None else ""
        if not path or _on_check_box(table, row, column):
            return
        if not open_file(path) and on_missing is not None:
            on_missing(path)

    table.cellClicked.connect(clicked)
