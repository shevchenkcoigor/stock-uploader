"""Run a long operation off the UI thread, with progress and cancellation."""

import threading
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

# work(progress, should_stop) -> result; progress(done, total, message)
Work = Callable[[Callable[[int, int, str], None], Callable[[], bool]], object]


class Task(QThread):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Work) -> None:
        super().__init__()
        self._work = work
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result = self._work(self.progress.emit, self._cancel.is_set)
        except Exception as error:  # shown in the window instead of dying silently in a thread
            self.failed.emit(f"{type(error).__name__}: {error}")
            return
        self.done.emit(result)
