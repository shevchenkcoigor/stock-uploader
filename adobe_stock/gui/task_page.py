"""Base class of the pages that start a long operation: progress bar, cancel button, output."""

import sqlite3
from collections.abc import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from adobe_stock.gui.context import AppContext
from adobe_stock.gui.worker import Task
from adobe_stock.gui.worker import Work as TaskWork

ProgressFn = Callable[[int, int, str], None]
StopFn = Callable[[], bool]
Work = Callable[[sqlite3.Connection, ProgressFn, StopFn], object]


class TaskPage(QWidget):
    """Subclasses fill `self.form` (a layout) and `self.button_row` (buttons) and call `start`."""

    def __init__(self, ctx: AppContext, heading: str, hint: str = "") -> None:
        super().__init__()
        self.ctx = ctx
        self._task: Task | None = None
        self._on_done: Callable[[object], None] | None = None
        self._locked: list[QPushButton] = []

        layout = QVBoxLayout(self)
        if heading:
            title = QLabel(heading)
            title.setProperty("role", "title")
            layout.addWidget(title)
        if hint:
            note = QLabel(hint)
            note.setWordWrap(True)
            layout.addWidget(note)
        self.form = QVBoxLayout()
        layout.addLayout(self.form)
        self.button_row = QHBoxLayout()
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        self.button_row.addStretch(1)
        self.button_row.addWidget(self.cancel_button)
        layout.addLayout(self.button_row)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)  # a long message wraps instead of widening the whole window
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)

    def add_action(self, button: QPushButton) -> QPushButton:
        """Put a button that must be disabled while an operation runs before the cancel button."""
        button.setProperty("primary", not self._locked)  # the first action of a page is its main one
        self.button_row.insertWidget(self.button_row.count() - 1, button)
        self._locked.append(button)
        return button

    def write(self, text: str) -> None:
        self.output.appendPlainText(text)

    @property
    def busy(self) -> bool:
        return self._task is not None

    def start(self, work: Work, on_done: Callable[[object], None]) -> None:
        """Run work(conn, progress, should_stop) in a thread; on_done(result) runs in the UI thread."""
        self._launch(self.ctx.job(work), on_done)

    def start_plain(
        self, work: Callable[[ProgressFn, StopFn], object], on_done: Callable[[object], None]
    ) -> None:
        """Like start, for work that does not touch the database."""
        self._launch(work, on_done)

    def _launch(self, runner: TaskWork, on_done: Callable[[object], None]) -> None:
        if self._task is not None:
            return
        self.output.clear()
        task = Task(runner)
        task.progress.connect(self._on_progress)
        self._on_done = on_done
        task.done.connect(self._finish)  # a method: runs in the UI thread
        task.failed.connect(self._fail)
        self._task = task
        self._set_running(True)
        task.start()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.status.setText(message)
        self.write(message)

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("Отмена: дожидаюсь окончания текущего шага…")

    def _finish(self, result: object) -> None:
        self._clear_task()
        on_done, self._on_done = self._on_done, None
        if on_done is not None:
            on_done(result)
        self.ctx.data_changed.emit()

    def _fail(self, message: str) -> None:
        self._clear_task()
        self.write(f"ОШИБКА: {message}")
        self.status.setText("Ошибка")

    def _clear_task(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.wait()
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        for button in self._locked:
            button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.progress.setVisible(running)
        if running:
            self.progress.setRange(0, 0)  # busy indicator until the first real progress arrives
            self.status.setText("Работаю…")

    def refresh(self) -> None:
        """Called when the page is shown or data changed; pages override it."""
