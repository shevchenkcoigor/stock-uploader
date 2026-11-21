"""The bottom strip of a list: '1–20 из 240' on the left, page buttons on the right."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

MAX_BUTTONS = 7  # more pages than this are shortened with an ellipsis
NEIGHBOURS = 1  # pages shown on each side of the current one when shortened
ELLIPSIS = None


def pages_in(total: int, page_size: int) -> int:
    return max(1, -(-total // page_size))


def visible_pages(count: int, current: int) -> list[int | None]:
    """Page numbers (0-based) to show as buttons; None is an ellipsis. First and last always stay."""
    if count <= MAX_BUTTONS:
        return list(range(count))
    wanted = {0, count - 1, *range(max(0, current - NEIGHBOURS), min(count, current + NEIGHBOURS + 1))}
    shown: list[int | None] = []
    previous = -1
    for number in sorted(wanted):
        if number - previous > 1:
            shown.append(ELLIPSIS)
        shown.append(number)
        previous = number
    return shown


class Pager(QWidget):
    page_selected = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.range_label = QLabel("")
        self.range_label.setProperty("role", "muted")
        row.addWidget(self.range_label)
        row.addStretch(1)
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(4)
        row.addLayout(self._buttons)

    def show_page(self, total: int, page_size: int, page: int) -> None:
        """Update the range text and the buttons for `total` items shown `page_size` per page."""
        first = page * page_size + 1 if total else 0
        last = min(total, (page + 1) * page_size)
        self.range_label.setText(f"{first}–{last} из {total}")
        while self._buttons.count():
            taken = self._buttons.takeAt(0)
            widget = taken.widget() if taken is not None else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        for number in visible_pages(pages_in(total, page_size), page):
            if number is None:
                gap = QLabel("…")
                gap.setProperty("role", "muted")
                self._buttons.addWidget(gap)
                continue
            button = QPushButton(str(number + 1))
            button.setProperty("page", True)
            button.setCheckable(True)
            button.setChecked(number == page)
            button.clicked.connect(lambda _=False, n=number: self.page_selected.emit(n))
            self._buttons.addWidget(button)
