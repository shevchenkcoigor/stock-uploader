"""The strip above the file list for editing several selected files at once."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton

from adobe_stock import state
from adobe_stock.categories import CATEGORIES
from adobe_stock.gui.status import view_of
from adobe_stock.gui.widgets import make_combo

MIN_BULK = 2  # below this a file is edited in the side panel instead


class BulkBar(QFrame):
    """A keyword is added to every selected file; the category and the status replace theirs.

    Empty fields change nothing.
    """

    applied = Signal(str, object, object)  # (keyword to add or "", category code or None, status or None)
    toggle_all = Signal()  # the checkbox shown with tiles; the list has one in its header

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("bulk")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        self.select_box = QCheckBox()  # marks / unmarks all; only for tiles, which have no header
        self.select_box.setVisible(False)
        self.select_box.clicked.connect(self.toggle_all)
        row.addWidget(self.select_box)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "heading")
        row.addWidget(self.count_label)
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("Тег для всех — Enter")
        self.tag_input.returnPressed.connect(self._apply)
        row.addWidget(self.tag_input, 1)
        self.category_box = make_combo()
        self.category_box.addItem("Категория", None)
        for code, name in CATEGORIES.items():
            self.category_box.addItem(name, str(code))
        row.addWidget(self.category_box)
        self.status_box = make_combo()
        self.status_box.addItem("Статус", None)
        for key in state.EDITABLE_STATUSES:
            self.status_box.addItem(view_of(key).label, key)
        row.addWidget(self.status_box)
        self.apply_button = QPushButton("Пометить")
        self.apply_button.clicked.connect(self._apply)
        row.addWidget(self.apply_button)
        self.set_count(0)

    def set_marks(self, marked: int, total: int) -> None:
        """Show how many files are marked; the checkbox is on when all are, partly when some are."""
        self.set_count(marked)
        self.select_box.setTristate(0 < marked < total)
        if marked == 0:
            state = Qt.CheckState.Unchecked
        else:
            state = Qt.CheckState.Checked if marked == total else Qt.CheckState.PartiallyChecked
        self.select_box.setCheckState(state)

    def set_count(self, count: int) -> None:
        """Always shown; editing needs two or more files (one is edited in the side panel)."""
        self.count_label.setText(f"Выбрано {count}")
        for widget in (self.tag_input, self.category_box, self.status_box, self.apply_button):
            widget.setEnabled(count >= MIN_BULK)

    def _apply(self) -> None:
        tag = self.tag_input.text().strip()
        category = self.category_box.currentData()
        status = self.status_box.currentData()
        if not tag and category is None and status is None:
            return
        self.applied.emit(tag, category, status)
        self.tag_input.clear()
        self.category_box.setCurrentIndex(0)
        self.status_box.setCurrentIndex(0)
