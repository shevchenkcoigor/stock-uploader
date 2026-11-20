"""What every page needs: preferences, the state database and how to run work against it."""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import QObject, Signal

from adobe_stock import services, state
from adobe_stock.gui.prefs import Preferences
from adobe_stock.gui.worker import Work

T = TypeVar("T")


class AppContext(QObject):
    data_changed = Signal()

    def __init__(self, prefs: Preferences) -> None:
        super().__init__()
        self.prefs = prefs

    @property
    def db_path(self) -> Path:
        return self.prefs.db_path

    def job(self, work: Callable[[sqlite3.Connection, Callable, Callable], T]) -> Work:
        """Wrap work(conn, progress, should_stop) for a worker thread: own connection, DB protection."""
        db = self.db_path

        def run(progress: Callable[[int, int, str], None], should_stop: Callable[[], bool]) -> object:
            db.parent.mkdir(parents=True, exist_ok=True)
            with services.protected(db):
                conn = state.open_db(db)
                try:
                    return work(conn, progress, should_stop)
                finally:
                    conn.close()

        return run

    def read(self, query: Callable[[sqlite3.Connection], T]) -> T:
        """A quick read on the UI thread (never creates or modifies the database)."""
        db = self.db_path
        if not db.exists():
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.executescript(state.SCHEMA)
        else:
            services.guard_state_db(db)
            conn = state.open_db(db)
        try:
            return query(conn)
        finally:
            conn.close()
