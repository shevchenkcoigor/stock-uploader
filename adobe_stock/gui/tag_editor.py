"""Keywords as chips: each has a × to remove it and can be renamed with a double click; new ones go
into a separate field and are added with Enter."""

from collections.abc import Iterable
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from adobe_stock.gui.flow_layout import FlowLayout
from adobe_stock.gui.widgets import ElidedLabel

REMOVE_GLYPH = "×"
TAG_MAX_WIDTH = 200  # a longer keyword is cut with "…" rather than widening the card


def parse_keywords(text: str) -> tuple[str, ...]:
    """'a, b,, a' -> ('a', 'b'): trimmed, no empty or repeated tags, order kept."""
    seen: dict[str, None] = {}
    for part in text.replace("\n", ",").split(","):
        tag = part.strip()
        if tag:
            seen.setdefault(tag, None)
    return tuple(seen)


class TagLabel(ElidedLabel):
    """The text of a chip; a double click asks to edit it."""

    edit_requested = Signal()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        self.edit_requested.emit()
        event.accept()


class TagChip(QFrame):
    removed = Signal(str)
    renamed = Signal(str, str)  # old, new

    def __init__(self, tag: str) -> None:
        super().__init__()
        self.setObjectName("tag")
        self.tag = tag
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 4, 0)
        row.setSpacing(2)
        self._label = TagLabel(tag, TAG_MAX_WIDTH)
        self._label.setToolTip(f"{tag}\nДвойной клик: изменить")
        self._label.edit_requested.connect(self._start_editing)
        self._remove = QToolButton()
        self._remove.setObjectName("tag-remove")
        self._remove.setText(REMOVE_GLYPH)
        self._remove.setToolTip("Удалить")
        self._remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove.clicked.connect(lambda: self.removed.emit(self.tag))
        self._editor: QLineEdit | None = None
        row.addWidget(self._label)
        row.addWidget(self._remove)

    def _start_editing(self) -> None:
        if self._editor is not None:
            return
        editor = QLineEdit(self.tag)
        editor.setObjectName("tag-edit")
        editor.setMinimumWidth(max(60, self._label.width()))
        editor.editingFinished.connect(self._finish_editing)
        self._editor = editor
        self._label.hide()
        cast(QHBoxLayout, self.layout()).insertWidget(0, editor)
        editor.setFocus()
        editor.selectAll()

    def _finish_editing(self) -> None:
        editor, self._editor = self._editor, None
        if editor is None:
            return
        text = editor.text().strip()
        editor.hide()
        editor.deleteLater()
        self._label.show()
        if text != self.tag:
            self.renamed.emit(self.tag, text)  # an empty text means: remove


class TagEditor(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._tags: tuple[str, ...] = ()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        self._title = QLabel("")
        self._title.setProperty("role", "muted")
        column.addWidget(self._title)
        self._chips = QWidget()
        self._flow = FlowLayout(self._chips)
        column.addWidget(self._chips)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Новый тег — Enter")
        self.input.returnPressed.connect(self._add_typed)
        column.addWidget(self.input)
        self._redraw()

    def tags(self) -> tuple[str, ...]:
        return self._tags

    def set_tags(self, tags: Iterable[str]) -> None:
        self._tags = parse_keywords(",".join(tags))
        self._redraw()

    def _commit(self, tags: tuple[str, ...]) -> None:
        if tags == self._tags:
            return
        self._tags = tags
        self._redraw()
        self.changed.emit()

    def _add_typed(self) -> None:
        added = parse_keywords(self.input.text())
        self.input.clear()
        self._commit(parse_keywords(",".join((*self._tags, *added))))

    def _remove(self, tag: str) -> None:
        self._commit(tuple(existing for existing in self._tags if existing != tag))

    def _rename(self, old: str, new: str) -> None:
        replaced = [new if existing == old else existing for existing in self._tags]
        self._commit(parse_keywords(",".join(replaced)))  # an empty or repeated name disappears

    def _redraw(self) -> None:
        self._title.setText(f"Ключевые слова · {len(self._tags)}")
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item is not None and (widget := item.widget()) is not None:
                widget.hide()
                widget.deleteLater()
        for tag in self._tags:
            chip = TagChip(tag)
            chip.removed.connect(self._remove)
            chip.renamed.connect(self._rename)
            self._flow.addWidget(chip)
        self._chips.updateGeometry()
