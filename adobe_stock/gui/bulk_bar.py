"""The strip above the file list for editing several selected files at once."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton

from adobe_stock.categories import CATEGORIES
from adobe_stock.gui.widgets import make_combo


class BulkBar(QFrame):
    """Keyword adds to every selected file, the category replaces theirs; empty fields change nothing."""

    applied = Signal(str, object)  # (keyword to add or "", category code or None)
    select_all_clicked = Signal()
    clear_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("bulk")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
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
        self.apply_button = QPushButton("Применить")
        self.apply_button.clicked.connect(self._apply)
        row.addWidget(self.apply_button)
        select_all = QPushButton("Выбрать все")
        select_all.clicked.connect(self.select_all_clicked)
        row.addWidget(select_all)
        clear = QPushButton("Снять выбор")
        clear.clicked.connect(self.clear_clicked)
        row.addWidget(clear)
        self.setVisible(False)

    def set_count(self, count: int) -> None:
        self.count_label.setText(f"Выбрано {count}")
        self.setVisible(count >= 2)

    def _apply(self) -> None:
        tag = self.tag_input.text().strip()
        category = self.category_box.currentData()
        if not tag and category is None:
            return
        self.applied.emit(tag, category)
        self.tag_input.clear()
        self.category_box.setCurrentIndex(0)
