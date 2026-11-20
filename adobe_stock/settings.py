"""Application settings: environment variables and an optional `.env` file.

Every field is read from `ADOBE_<FIELD NAME>`, e.g. `ADOBE_SFTP_HOST`. Real environment
variables win over the `.env` file. Never commit `.env`.
"""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SFTP_HOST = "sftp.contributor.adobestock.com"
DEFAULT_SFTP_PORT = 22
MAX_PORT = 65535
DEFAULT_CSV_DIR = Path("csv")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ADOBE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # unrelated keys in .env are fine
        frozen=True,
    )

    sftp_host: str = DEFAULT_SFTP_HOST
    sftp_port: int = Field(default=DEFAULT_SFTP_PORT, ge=1, le=MAX_PORT)
    sftp_user: str = ""
    sftp_password: SecretStr = SecretStr("")
    backup_dir: Path | None = Field(default=None, description="default: <db name>_backups next to the DB")
    csv_dir: Path = DEFAULT_CSV_DIR

    @field_validator("sftp_host")
    @classmethod
    def _bare_hostname(cls, value: str) -> str:
        """Accept `sftp://host/` or `host:22` pasted from a docs page; DNS needs just the name."""
        host = value.strip().removeprefix("sftp://").removeprefix("ssh://").split("/", 1)[0]
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if not host:
            raise ValueError("ADOBE_SFTP_HOST is empty")
        return host

    def require_sftp_credentials(self) -> None:
        if not self.sftp_user or not self.sftp_password.get_secret_value():
            raise RuntimeError("Set ADOBE_SFTP_USER and ADOBE_SFTP_PASSWORD (environment or .env)")


def load_settings() -> Settings:
    """Read settings fresh each time, so environment changes are always picked up."""
    return Settings()
