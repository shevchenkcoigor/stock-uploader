"""Preferences: database file, SFTP login, title label, CSV folder, Claude location."""

import subprocess
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from adobe_stock import caption, sftp_upload
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.task_page import TaskPage
from adobe_stock.settings import DEFAULT_AI_TITLE_LABEL, DEFAULT_SFTP_HOST, DEFAULT_SFTP_PORT

CLAUDE_CHECK_TIMEOUT_SECONDS = 30


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
        self.csv_dir = QLineEdit(str(prefs.csv_dir))
        form.addRow("Папка для CSV", self._with_browse(self.csv_dir, self._browse_csv))
        self.claude_path = QLineEdit(prefs.text("claude_path"))
        self.claude_path.setPlaceholderText("пусто = искать автоматически")
        form.addRow("Путь к claude", self.claude_path)
        self.form.addLayout(form)

        self.save_button = self.add_action(QPushButton("Сохранить"))
        self.save_button.clicked.connect(self._save)
        self.claude_button = self.add_action(QPushButton("Проверить Claude"))
        self.claude_button.clicked.connect(self._check_claude)
        self.sftp_button = self.add_action(QPushButton("Проверить SFTP"))
        self.sftp_button.clicked.connect(self._check_sftp)

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

    def _browse_csv(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Папка для CSV", self.csv_dir.text())
        if chosen:
            self.csv_dir.setText(chosen)

    def _save(self) -> bool:
        prefs = self.ctx.prefs
        try:
            int(self.port.text())
        except ValueError:
            self.write("Порт должен быть числом.")
            return False
        prefs.set_text("db_path", str(Path(self.db_path.text().strip()).expanduser()))
        prefs.set_text("sftp_host", self.host.text().strip())
        prefs.set_text("sftp_port", self.port.text().strip())
        prefs.set_text("sftp_user", self.user.text().strip())
        prefs.set_text("ai_title_label", self.label.text().strip())
        prefs.set_text("csv_dir", str(Path(self.csv_dir.text().strip()).expanduser()))
        prefs.set_text("claude_path", self.claude_path.text().strip())
        try:
            if self.password.text():
                prefs.secrets.set(self.password.text())
                self.password.clear()
            prefs.build_settings()
        except (ValidationError, ValueError) as error:
            self.write(f"Неверные настройки: {error}")
            return False
        except Exception as error:  # keychain problems (no backend, access denied)
            self.write(f"Не удалось сохранить пароль в связке ключей: {error}")
            return False
        prefs.apply_environment()
        self.write("Сохранено.")
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
