"""Step 3: SFTP upload of a batch: a queue of its files with the state and progress of each."""

from collections.abc import Sequence

from pydantic import ValidationError
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from adobe_stock import services, state
from adobe_stock.gui import upload_queue as queue
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.description_page import format_size
from adobe_stock.gui.file_actions import FILE_PATH_ROLE, attach_file_menu, open_on_double_click
from adobe_stock.gui.header_sort import HeaderSort
from adobe_stock.gui.pager import Pager, pages_in
from adobe_stock.gui.status import StatusView, batch_status_text, drop_cell_widgets, view_cell
from adobe_stock.gui.task_page import TaskPage
from adobe_stock.gui.thumbnails import tile_icon
from adobe_stock.gui.widgets import make_combo, mono_font

COLUMNS = ("Файл", "Размер", "Прогресс", "Статус")
SORT_FIELDS = ("name", "size", "progress", "status")
COL_NAME, COL_SIZE, COL_PROGRESS, COL_STATUS = range(4)
PAGE_SIZE = 20
PROGRESS_STEPS = 1000  # resolution of the overall bar
FILE_BAR_STEPS = 100
THUMB_SIDE = 40
KEYCHAIN_NOTICE = (
    "Сейчас macOS может попросить пароль от Mac: так приложение получает сохранённый SFTP-пароль. "
    "Выберите «Всегда разрешать»."
)
ROW_HEIGHT = THUMB_SIDE + 12
STATE_VIEWS = {
    queue.FAILED: StatusView("Ошибка", "danger", 0),
    queue.UPLOADING: StatusView("Загружается", "accent", 1),
    queue.QUEUED: StatusView("В очереди", "ink-muted", 2),
    queue.DONE: StatusView("Загружен", "success", 3),
}


def summary_text(totals: queue.QueueTotals) -> str:
    text = f"{totals.done} из {totals.total} загружено"
    if totals.failed:
        text += f" · {totals.failed} {_errors_word(totals.failed)}"
    return text


def _errors_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "ошибка"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "ошибки"
    return "ошибок"


def progress_text(totals: queue.QueueTotals) -> str:
    percent = round(totals.fraction * 100)
    return f"{percent}% · {format_size(int(totals.sent_bytes))[:-3]} из {format_size(totals.total_bytes)}"


class UploadPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "")
        self._rows: list[queue.UploadRow] = []
        self._page = 0
        form = QFormLayout()
        self.batch = make_combo()
        form.addRow("Батч", self.batch)
        self.form.addLayout(form)

        self.run_button = self.add_action(QPushButton("Загрузить"))  # the first action is the main one
        self.retry_button = self.add_action(QPushButton("Повторить ошибки"))
        self.cancel_button.setText("Пауза")
        self.run_button.clicked.connect(self._run)
        self.retry_button.clicked.connect(self._run)
        self.batch.currentIndexChanged.connect(self._fill_files)

        self.queue_title = QLabel("Очередь загрузки")
        self.queue_title.setProperty("role", "heading")
        self.summary = QLabel("")
        self.summary.setProperty("role", "muted")
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.queue_title)
        titles.addWidget(self.summary)
        head = QHBoxLayout()
        head.addLayout(titles, 1)
        for button in (self.retry_button, self.cancel_button, self.run_button):
            head.addWidget(button)

        self.progress.setRange(0, PROGRESS_STEPS)
        self.progress.setVisible(True)  # the queue always shows how far it is
        self.progress_label = QLabel("")
        self.progress_label.setProperty("role", "muted")
        bar_row = QHBoxLayout()
        bar_row.addWidget(self.progress, 1)
        bar_row.addWidget(self.progress_label)

        self.files = self._make_table()
        self.pager = Pager()
        self.pager.page_selected.connect(self._go_page)
        card = QFrame()
        card.setObjectName("panel")
        inside = QVBoxLayout(card)
        inside.addLayout(head)
        inside.addLayout(bar_row)
        inside.addWidget(self.files, 1)
        inside.addWidget(self.pager)
        self.form.addWidget(card, 1)
        self.output.setVisible(False)  # the details are in «Журнал»; the status line says what happened

        self.sorter = HeaderSort(
            self.files, COLUMNS, SORT_FIELDS, field="status", descending=False, on_change=self._resort
        )

    def _make_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setIconSize(QSize(THUMB_SIDE, THUMB_SIDE))
        table.setShowGrid(False)
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for column, width in ((COL_SIZE, 100), (COL_PROGRESS, 240), (COL_STATUS, 150)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(column, width)
        open_on_double_click(table, [COL_NAME])
        attach_file_menu(table, lambda pos: self._path_at(table, pos))
        return table

    @staticmethod
    def _path_at(table: QTableWidget, position) -> str:
        item = table.itemAt(position)
        name = table.item(item.row(), COL_NAME) if item is not None else None
        return str(name.data(FILE_PATH_ROLE) or "") if name is not None else ""

    # ------------------------------------------------------------------ what is shown

    def refresh(self) -> None:
        rows = self.ctx.read(state.pending_batches)
        statuses = self.ctx.read(state.batch_status_counts)
        self.batch.blockSignals(True)
        self.batch.clear()
        for row in rows:
            status = batch_status_text(statuses.get(row["batch_id"], ()))
            label = (
                f"Батч {row['batch_id']}: {row['folder']}, часть {row['part']} — {row['n']} файлов, "
                f"статус: {status}"
            )
            self.batch.addItem(label, row["batch_id"])
        if rows:
            self.batch.setCurrentIndex(self.batch.count() - 1)  # the last pending batch by default
        self.batch.blockSignals(False)
        if not self.busy:
            self.status.setText("" if rows else "Нет файлов, ожидающих загрузки")
        self._fill_files()

    def _fill_files(self) -> None:
        """Read the chosen batch and show its queue."""
        batch = self.batch.currentData()
        files = [] if batch is None else self.ctx.read(lambda conn: state.batch_files(conn, int(batch)))
        self.show_queue(queue.rows_from_db(files, self.busy))

    def show_queue(self, rows: Sequence[queue.UploadRow]) -> None:
        self._rows = list(rows)
        self._render()

    def _resort(self) -> None:
        self._page = 0
        self._render()

    def _go_page(self, number: int) -> None:
        self._page = number
        self._render()

    def _render(self) -> None:
        ordered = queue.sorted_rows(self._rows, self.sorter.field, self.sorter.descending)
        self._page = min(self._page, pages_in(len(ordered), PAGE_SIZE) - 1)
        rows = ordered[self._page * PAGE_SIZE : (self._page + 1) * PAGE_SIZE]
        self.pager.show_page(len(ordered), PAGE_SIZE, self._page)
        drop_cell_widgets(self.files, COL_PROGRESS)
        drop_cell_widgets(self.files, COL_STATUS)
        self.files.setRowCount(len(rows))
        for index, row in enumerate(rows):
            name = QTableWidgetItem(row.filename)
            name.setFont(mono_font())
            name.setIcon(tile_icon(row.path, THUMB_SIDE))
            name.setToolTip(row.path)
            name.setData(FILE_PATH_ROLE, row.path)
            self.files.setItem(index, COL_NAME, name)
            self.files.setItem(index, COL_SIZE, QTableWidgetItem(format_size(row.size)))
            self.files.setCellWidget(index, COL_PROGRESS, self._file_bar(row.progress))
            self.files.setCellWidget(index, COL_STATUS, view_cell(STATE_VIEWS[row.state]))
        totals = queue.totals_of(self._rows)
        self.summary.setText(summary_text(totals) if ordered else "В очереди пусто")
        self.progress.setValue(round(totals.fraction * PROGRESS_STEPS))
        self.progress_label.setText(progress_text(totals) if ordered else "")
        self._sync_buttons(totals)

    @staticmethod
    def _file_bar(fraction: float) -> QWidget:
        bar = QProgressBar()
        bar.setRange(0, FILE_BAR_STEPS)
        bar.setValue(round(fraction * FILE_BAR_STEPS))
        holder = QWidget()
        inner = QHBoxLayout(holder)
        inner.setContentsMargins(8, 0, 16, 0)
        inner.addWidget(bar)
        return holder

    def _sync_buttons(self, totals: queue.QueueTotals) -> None:
        waiting = totals.done < totals.total
        self.run_button.setEnabled(waiting and not self.busy)
        self.retry_button.setEnabled(bool(totals.failed) and not self.busy)

    # ------------------------------------------------------------------ the run

    def _set_running(self, running: bool) -> None:
        super()._set_running(running)
        self.progress.setVisible(True)  # the queue always shows how far it is
        self.progress.setRange(0, PROGRESS_STEPS)
        if not running:
            return
        self._fill_files()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self.write(message)
        self.status.setText(message)
        self._fill_files()

    def _announce_keychain_prompt(self) -> None:
        """Say what the macOS password prompt is before it appears (it blocks the window)."""
        self.write(KEYCHAIN_NOTICE)
        self.status.setText(KEYCHAIN_NOTICE)
        QApplication.processEvents()  # show the text now: the keychain read blocks the UI thread

    def _run(self) -> None:
        batch = self.batch.currentData()
        if batch is None:
            return
        if self.ctx.prefs.secrets.needs_unlock:
            self._announce_keychain_prompt()
        try:
            settings = self.ctx.prefs.build_settings()
        except (ValidationError, ValueError) as error:
            self.write(f"Проверьте настройки SFTP: {error}")
            self.status.setText(f"Проверьте настройки SFTP: {error}")
            return
        if not settings.sftp_user or not settings.sftp_password.get_secret_value():
            self.write(
                "Не заданы логин и пароль SFTP. Откройте «Настройки», введите их и нажмите «Сохранить»; "
                "кнопка «Проверить SFTP» входит на сервер, ничего не загружая."
            )
            self.status.setText("Нужны логин и пароль SFTP: введите их в «Настройках»")
            return

        def work(conn, progress, should_stop):
            return services.upload_batch(conn, int(batch), settings, progress, should_stop)

        self.start(work, self._done)

    def _done(self, report: object) -> None:
        if not isinstance(report, services.UploadReport):
            return
        self.refresh()  # first: it clears the status line, the result below is written after it
        if report.error:
            self.write(f"ОШИБКА {report.error}")
            self.write("Файлы остались в очереди. Проверьте SFTP в «Настройках» (кнопка «Проверить SFTP»).")
            self.status.setText(f"Не удалось загрузить: {report.error}")
        else:
            self.write(f"Загружено: {report.uploaded} из {report.total}, ошибок: {len(report.failed)}")
            if report.stopped:
                self.write("Приостановлено пользователем.")
            if report.uploaded and not report.failed:
                self.write("Дальше: вкладка «CSV и отправка».")
            self.status.setText("Готово" if report.ok else "Готово, есть ошибки: «Повторить ошибки»")
