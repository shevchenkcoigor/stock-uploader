"""Preferences of the desktop app.

Plain values live in QSettings (a per-user file); the SFTP password lives in the operating system's
keychain (macOS Keychain, Windows Credential Manager), never in a file.
"""

import os
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr
from PySide6.QtCore import QSettings, QStandardPaths

from adobe_stock import caption
from adobe_stock.services import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CAPTION_LIMIT,
    DEFAULT_CAPTION_WORKERS,
    DEFAULT_IMAGES_PER_CALL,
)
from adobe_stock.settings import DEFAULT_AI_TITLE_LABEL, DEFAULT_SFTP_HOST, DEFAULT_SFTP_PORT, Settings

APP_NAME = "Adobe Stock Uploader"
KEYCHAIN_SERVICE = "adobe-stock-uploader"
KEYCHAIN_ACCOUNT = "sftp"


class SecretStore(Protocol):
    def get(self) -> str: ...

    def set(self, value: str) -> None: ...


class KeychainStore:
    """The SFTP password in the OS keychain."""

    def get(self) -> str:
        import keyring

        return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT) or ""

    def set(self, value: str) -> None:
        import keyring

        if value:
            keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, value)
        elif self.get():
            keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


class MemoryStore:
    """For tests: keeps the secret in memory."""

    def __init__(self, value: str = "") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


def app_data_dir() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))


class Preferences:
    def __init__(self, store: QSettings, secrets: SecretStore) -> None:
        self._store = store
        self.secrets = secrets

    def text(self, key: str, default: str = "") -> str:
        value = self._store.value(key, default)
        return str(value) if value is not None else default

    def set_text(self, key: str, value: str) -> None:
        self._store.setValue(key, value)
        self._store.sync()  # written now, not at some later point

    @property
    def db_path(self) -> Path:
        return Path(self.text("db_path") or str(app_data_dir() / "state.db"))

    @property
    def csv_dir(self) -> Path:
        return Path(self.text("csv_dir") or str(app_data_dir() / "csv"))

    @property
    def batch_size(self) -> int:
        return int(self.text("batch_size", str(DEFAULT_BATCH_SIZE)))

    @property
    def caption_limit(self) -> int:
        return int(self.text("caption_limit", str(DEFAULT_CAPTION_LIMIT)))

    @property
    def caption_workers(self) -> int:
        return int(self.text("caption_workers", str(DEFAULT_CAPTION_WORKERS)))

    @property
    def caption_per_call(self) -> int:
        return int(self.text("caption_per_call", str(DEFAULT_IMAGES_PER_CALL)))

    @property
    def model(self) -> str:
        saved = self.text("model", caption.DEFAULT_MODEL)
        return saved if saved in caption.MODEL_IDS else caption.DEFAULT_MODEL

    def build_settings(self, *, with_password: bool = True) -> Settings:
        """Settings for the services. Reads no .env: everything comes from the app's own preferences.

        Without the password the keychain is not touched (reading it makes macOS ask for the Mac password).
        """
        return Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            sftp_host=self.text("sftp_host", DEFAULT_SFTP_HOST),
            sftp_port=int(self.text("sftp_port", str(DEFAULT_SFTP_PORT))),
            sftp_user=self.text("sftp_user"),
            sftp_password=SecretStr(self.secrets.get() if with_password else ""),
            csv_dir=self.csv_dir,
            ai_title_label=self.text("ai_title_label", DEFAULT_AI_TITLE_LABEL),
        )

    def apply_environment(self) -> None:
        """Hand the optional Claude path to the caption module (it reads ADOBE_CLAUDE_PATH)."""
        path = self.text("claude_path").strip()
        if path:
            os.environ[caption.CLAUDE_PATH_ENV] = path
        else:
            os.environ.pop(caption.CLAUDE_PATH_ENV, None)
