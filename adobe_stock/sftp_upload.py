"""SFTP upload to Adobe Stock. Credentials come from environment variables only."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import paramiko

DEFAULT_HOST = "sftp.contributor.adobestock.com"
DEFAULT_PORT = 22


@dataclass(frozen=True)
class SftpConfig:
    host: str
    port: int
    username: str
    password: str

    @classmethod
    def from_env(cls) -> "SftpConfig":
        username = os.environ.get("ADOBE_SFTP_USER")
        password = os.environ.get("ADOBE_SFTP_PASSWORD")
        if not username or not password:
            raise RuntimeError("Set ADOBE_SFTP_USER and ADOBE_SFTP_PASSWORD")
        return cls(
            host=os.environ.get("ADOBE_SFTP_HOST", DEFAULT_HOST),
            port=int(os.environ.get("ADOBE_SFTP_PORT", DEFAULT_PORT)),
            username=username,
            password=password,
        )


def upload_files(
    config: SftpConfig,
    files: list[Path],
    on_success: Callable[[Path], None] = lambda _path: None,
) -> list[str]:
    """Upload files to the account root; return names that failed.

    on_success runs right after each file, so progress survives an interrupted run.
    """
    transport = paramiko.Transport((config.host, config.port))
    failed: list[str] = []
    try:
        transport.connect(username=config.username, password=config.password)
        client = paramiko.SFTPClient.from_transport(transport)
        for path in files:
            try:
                client.put(str(path), path.name)
                on_success(path)
                print(f"uploaded {path.name}")
            except (OSError, paramiko.SSHException) as error:
                print(f"FAILED {path.name}: {error}")
                failed.append(path.name)
    finally:
        transport.close()
    return failed
