"""Sorting by a click on a table header: ↕ on the other columns, ↑ / ↓ on the active one."""

from collections.abc import Callable, Sequence

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from adobe_stock.gui.theme import active_colors

ASCENDING = "↑"
DESCENDING = "↓"
UNSORTED = "↕"


class HeaderSort:
    """Keeps the sort column and direction of one table; the table's own sort arrow is not used.

    `fields[i]` is the sort field of column i, or None when that column cannot be sorted.
    A click on another column sorts it ascending; a click on the active column flips the direction.
    """

    def __init__(
        self,
        table: QTableWidget,
        titles: Sequence[str],
        fields: Sequence[str | None],
        field: str,
        descending: bool,
        on_change: Callable[[], None],
    ) -> None:
        self._table = table
        self._titles = tuple(titles)
        self._fields = tuple(fields)
        self.field = field
        self.descending = descending
        self._on_change = on_change
        header = table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)  # we draw our own arrows; Qt's would flip on its own too
        header.sectionClicked.connect(self.click)
        self.refresh()

    def click(self, section: int) -> None:
        field = self._fields[section]
        if field is None:
            return
        self.set(field, not self.descending if field == self.field else False)
        self._on_change()

    def set(self, field: str, descending: bool) -> None:
        self.field, self.descending = field, descending
        self.refresh()

    def label(self, section: int) -> str:
        field = self._fields[section]
        if field is None:
            return self._titles[section]
        arrow = (DESCENDING if self.descending else ASCENDING) if field == self.field else UNSORTED
        return f"{self._titles[section]} {arrow}"

    def refresh(self) -> None:
        colors = active_colors()
        for section, field in enumerate(self._fields):
            item = QTableWidgetItem(self.label(section))
            active = field is not None and field == self.field
            item.setForeground(QBrush(QColor(colors["accent" if active else "ink-muted"])))
            self._table.setHorizontalHeaderItem(section, item)
