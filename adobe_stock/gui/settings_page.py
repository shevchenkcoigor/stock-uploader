"""Preferences: database file, SFTP login, title label, CSV folder, Claude location."""

import subprocess
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from adobe_stock import caption, sftp_upload
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.task_page import TaskPage
from adobe_stock.gui.theme import THEME_CHOICES, THEME_KEY, THEME_SYSTEM, apply_theme
from adobe_stock.gui.widgets import make_combo
from adobe_stock.settings import DEFAULT_AI_TITLE_LABEL, DEFAULT_SFTP_HOST, DEFAULT_SFTP_PORT

CLAUDE_CHECK_TIMEOUT_SECONDS = 30
MAX_BATCH_SIZE = 20_000
MAX_CAPTION_WORKERS = 8
MAX_IMAGES_PER_CALL = 20


def check_claude(progress, should_stop) -> str:
    """Where `claude` is and which version answers; raises with a hint when it does not run."""
    found = caption.find_claude()
    if found is None:
        raise RuntimeError(
            "Claude Code (`claude`) не найден. Установите его и войдите в аккаунт, либо укажите путь выше."
        )
    completed = subprocess.run(
        [found, "--version"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=CLAUDE_CHECK_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"`{found} --version` завершился с кодом {completed.returncode}: {completed.stderr}"
        )
    return f"Claude найден: {found}\nВерсия: {completed.stdout.strip()}"


class SettingsPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            ctx,
            "Настройки",
            "Пароль SFTP хранится в системной связке ключей (macOS Keychain / Windows Credential Manager), "
            "остальное в файле настроек пользователя.",
        )
        prefs = ctx.prefs
        form = QFormLayout()
        self.db_path = QLineEdit(str(prefs.db_path))
        form.addRow("Файл базы (state.db)", self._with_browse(self.db_path, self._browse_db))
        self.host = QLineEdit(prefs.text("sftp_host", DEFAULT_SFTP_HOST))
        form.addRow("SFTP хост", self.host)
        self.port = QLineEdit(prefs.text("sftp_port", str(DEFAULT_SFTP_PORT)))
        form.addRow("SFTP порт", self.port)
        self.user = QLineEdit(prefs.text("sftp_user"))
        form.addRow("SFTP логин", self.user)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("оставьте пустым, чтобы не менять сохранённый")
        form.addRow("SFTP пароль", self.password)
        self.label = QLineEdit(prefs.text("ai_title_label", DEFAULT_AI_TITLE_LABEL))
        self.label.setPlaceholderText("пусто = не добавлять")
        form.addRow("Метка в конце заголовка", self.label)
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, MAX_BATCH_SIZE)
        self.batch_size.setValue(prefs.batch_size)
        form.addRow("Файлов в батче", self.batch_size)
        self.caption_limit = self._spin(1, MAX_BATCH_SIZE, prefs.caption_limit)
        form.addRow("Файлов за одно нажатие «Caption»", self.caption_limit)
        self.caption_workers = self._spin(1, MAX_CAPTION_WORKERS, prefs.caption_workers)
        form.addRow("Параллельных вызовов Claude", self.caption_workers)
        self.caption_per_call = self._spin(1, MAX_IMAGES_PER_CALL, prefs.caption_per_call)
        form.addRow("Картинок в одном вызове", self.caption_per_call)
        self.csv_dir = QLineEdit(str(prefs.csv_dir))
        form.addRow("Папка для CSV", self._with_browse(self.csv_dir, self._browse_csv))
        self.claude_path = QLineEdit(prefs.text("claude_path"))
        self.claude_path.setPlaceholderText("пусто = искать автоматически")
        form.addRow("Путь к claude", self.claude_path)
        self.theme = make_combo()
        for label, value in THEME_CHOICES:
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(prefs.text(THEME_KEY, THEME_SYSTEM))))
        form.addRow("Тема", self.theme)
        self.form.addLayout(form)

        self.cancel_button.setVisible(
            False
        )  # the checks cannot be interrupted, and nothing is left to cancel
        self.claude_button = self.add_action(QPushButton("Проверить Claude"))
        self.claude_button.clicked.connect(self._check_claude)
        self.sftp_button = self.add_action(QPushButton("Проверить SFTP"))
        self.sftp_button.clicked.connect(self._check_sftp)

        self._saved = self._snapshot()
        for field in (
            self.db_path,
            self.host,
            self.port,
            self.user,
            self.password,
            self.label,
            self.csv_dir,
            self.claude_path,
            self.batch_size,
            self.caption_limit,
            self.caption_workers,
            self.caption_per_call,
        ):  # a change is applied when the field is left or Enter is pressed
            field.editingFinished.connect(self._autosave)
        self.theme.currentIndexChanged.connect(lambda _index: self._autosave())

    def _snapshot(self) -> tuple[object, ...]:
        """Everything the page stores (but the password), to see whether anything changed."""
        return (
            self.db_path.text().strip(),
            self.host.text().strip(),
            self.port.text().strip(),
            self.user.text().strip(),
            self.label.text().strip(),
            self.csv_dir.text().strip(),
            self.claude_path.text().strip(),
            self.batch_size.value(),
            self.caption_limit.value(),
            self.caption_workers.value(),
            self.caption_per_call.value(),
            self.theme.currentData(),
        )

    @staticmethod
    def _spin(low: int, high: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        return spin

    def _autosave(self) -> None:
        if self._snapshot() != self._saved or self.password.text():
            self._save()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._autosave()  # what was typed in the last field is applied when the page is left
        super().hideEvent(event)

    @staticmethod
    def _with_browse(field: QLineEdit, handler) -> QWidget:
        button = QPushButton("Выбрать…")
        button.clicked.connect(handler)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field, 1)
        row.addWidget(button)
        holder = QWidget()
        holder.setLayout(row)
        return holder

    def _browse_db(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Файл базы",
            self.db_path.text(),
            "SQLite (*.db);;Все файлы (*)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if chosen:
            self.db_path.setText(chosen)
            self._autosave()

    def _browse_csv(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Папка для CSV", self.csv_dir.text())
        if chosen:
            self.csv_dir.setText(chosen)
            self._autosave()

    @staticmethod
    def _path_text(field: QLineEdit) -> str:
        """An emptied field means the default place, not the current folder."""
        text = field.text().strip()
        return str(Path(text).expanduser()) if text else ""

    def _save(self) -> bool:
        prefs = self.ctx.prefs
        try:
            int(self.port.text())
        except ValueError:
            self.write("Порт должен быть числом.")
            return False
        prefs.set_text("db_path", self._path_text(self.db_path))
        prefs.set_text("sftp_host", self.host.text().strip())
        prefs.set_text("sftp_port", self.port.text().strip())
        prefs.set_text("sftp_user", self.user.text().strip())
        prefs.set_text("ai_title_label", self.label.text().strip())
        prefs.set_text("csv_dir", self._path_text(self.csv_dir))
        prefs.set_text("batch_size", str(self.batch_size.value()))
        prefs.set_text("caption_limit", str(self.caption_limit.value()))
        prefs.set_text("caption_workers", str(self.caption_workers.value()))
        prefs.set_text("caption_per_call", str(self.caption_per_call.value()))
        prefs.set_text("claude_path", self.claude_path.text().strip())
        prefs.set_text(THEME_KEY, str(self.theme.currentData()))
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, str(self.theme.currentData()))
        try:
            if self.password.text():
                prefs.secrets.set(self.password.text())
                self.password.clear()
            prefs.build_settings(with_password=False)  # only checks the values: no keychain read
        except (ValidationError, ValueError) as error:
            self.write(f"Неверные настройки: {error}")
            return False
        except Exception as error:  # keychain problems (no backend, access denied)
            self.write(f"Не удалось сохранить пароль в связке ключей: {error}")
            return False
        prefs.apply_environment()
        self._saved = self._snapshot()
        self.status.setText("Сохранено")
        self.ctx.data_changed.emit()
        return True

    def _check_claude(self) -> None:
        if self._save():
            self.start_plain(check_claude, lambda result: self.write(str(result)))

    def _check_sftp(self) -> None:
        if not self._save():
            return
        try:
            settings = self.ctx.prefs.build_settings()
        except (ValidationError, ValueError) as error:
            self.write(f"Неверные настройки: {error}")
            return

        def work(progress, should_stop) -> str:
            sftp_upload.check_connection(settings)
            return f"Вход выполнен: {settings.sftp_user}@{settings.sftp_host}:{settings.sftp_port}"

        self.start_plain(work, lambda result: self.write(str(result)))
