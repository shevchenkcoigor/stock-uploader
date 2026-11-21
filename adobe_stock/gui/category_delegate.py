"""Edit a file's category in place: a double-click on the cell opens the list of Adobe categories."""

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex
from PySide6.QtWidgets import QComboBox, QStyleOptionViewItem, QWidget

from adobe_stock.categories import CATEGORIES
from adobe_stock.gui.empty_cell import EmptyCellDelegate
from adobe_stock.gui.widgets import make_combo

NO_CATEGORY = "—"


def category_code(name: str) -> str:
    """The stored code for a category name; empty for none or an unknown name."""
    for code, known in CATEGORIES.items():
        if known == name:
            return str(code)
    return ""


class CategoryDelegate(EmptyCellDelegate):
    def createEditor(  # noqa: N802
        self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QWidget:
        combo = make_combo()
        combo.setParent(parent)
        combo.addItem(NO_CATEGORY)
        combo.addItems(list(CATEGORIES.values()))
        combo.activated.connect(lambda _index: self._commit(combo))  # a choice ends the edit at once
        return combo

    def _commit(self, combo: QComboBox) -> None:
        self.commitData.emit(combo)
        self.closeEditor.emit(combo)

    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:  # noqa: N802
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(max(0, editor.findText(str(index.data() or NO_CATEGORY))))

    def setModelData(  # noqa: N802
        self, editor: QWidget, model: QAbstractItemModel, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        if isinstance(editor, QComboBox):
            text = editor.currentText()
            model.setData(index, "" if text == NO_CATEGORY else text)

    def updateEditorGeometry(  # noqa: N802
        self, editor: QWidget, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        editor.setGeometry(option.rect)  # type: ignore[attr-defined]
