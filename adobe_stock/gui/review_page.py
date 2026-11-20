"""Step 3: files Claude flagged as possible Adobe AI-policy risks wait here for a human decision."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QPushButton, QTableWidget, QTableWidgetItem

from adobe_stock import services, state
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.task_page import TaskPage

THUMB_SIDE = 96
COLUMNS = ("Превью", "Файл", "Заголовок", "Почему задержан")


def thumbnail(path: str, side: int = THUMB_SIDE) -> QIcon:
    """Fast scaled decode, so even a 100 MP JPEG does not freeze the window."""
    reader = QImageReader(path)
    size = reader.size()
    if size.isValid():
        size.scale(side, side, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    image = reader.read()
    return QIcon() if image.isNull() else QIcon(QPixmap.fromImage(image))


class ReviewPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            ctx,
            "Проверка",
            "Claude задержал эти файлы: на картинке могут быть реальный человек, персонаж, бренд, чужая "
            "работа и т. п. Посмотрите и одобрите те, что можно отправлять. Остальные удалите из папки.",
        )
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setIconSize(QSize(THUMB_SIDE, THUMB_SIDE))
        self.table.verticalHeader().setDefaultSectionSize(THUMB_SIDE + 8)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, THUMB_SIDE + 16)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 260)
        self.form.addWidget(self.table, 1)
        self.approve_selected = self.add_action(QPushButton("Одобрить выбранные"))
        self.approve_selected.clicked.connect(lambda: self._approve(selected_only=True))
        self.approve_all = self.add_action(QPushButton("Одобрить все"))
        self.approve_all.clicked.connect(lambda: self._approve(selected_only=False))

    def refresh(self) -> None:
        rows = self.ctx.read(state.review_files)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            icon_item = QTableWidgetItem()
            icon_item.setIcon(thumbnail(row["path"]))
            self.table.setItem(index, 0, icon_item)
            name_item = QTableWidgetItem(row["filename"])
            name_item.setToolTip(row["path"])
            self.table.setItem(index, 1, name_item)
            self.table.setItem(index, 2, QTableWidgetItem(row["title"]))
            self.table.setItem(index, 3, QTableWidgetItem(row["error"] or ""))
        self.status.setText(f"Файлов на проверке: {len(rows)}")

    def _names(self, selected_only: bool) -> list[str] | None:
        if not selected_only:
            return None
        rows = {index.row() for index in self.table.selectedIndexes()}
        return [item.text() for row in sorted(rows) if (item := self.table.item(row, 1))]

    def _approve(self, selected_only: bool) -> None:
        names = self._names(selected_only)
        if names is not None and not names:
            self.write("Выберите строки в таблице.")
            return
        batch_size = self.ctx.prefs.batch_size

        def work(conn, progress, should_stop):
            return services.approve(conn, names, batch_size)

        self.start(work, self._done)

    def _done(self, report: object) -> None:
        if isinstance(report, services.ApproveReport):
            self.write(f"Одобрено файлов: {report.count}, новых батчей: {report.new_batches}")
        self.refresh()
