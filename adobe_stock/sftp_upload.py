"""SFTP upload to Adobe Stock. Connection settings come from `adobe_stock.settings`."""

from collections.abc import Callable
from pathlib import Path

import paramiko

from adobe_stock.settings import Settings


def _ignore_failure(remote_name: str, error: str) -> None:
    return None


def _never_stop() -> bool:
    return False


def upload_files(
    settings: Settings,
    files: list[tuple[Path, str]],
    on_success: Callable[[Path], None] = lambda _path: None,
    on_failure: Callable[[str, str], None] = _ignore_failure,
    should_stop: Callable[[], bool] = _never_stop,
) -> list[str]:
    """Upload (local path, remote name) pairs to the account root; return names that failed.

    on_success runs right after each file, so progress survives an interrupted run.
    on_failure(remote_name, error) runs for a file that could not be uploaded.
    should_stop is checked before each file; files not reached are neither uploaded nor failed.
    """
    settings.require_sftp_credentials()
    transport = paramiko.Transport((settings.sftp_host, settings.sftp_port))
    failed: list[str] = []
    try:
        transport.connect(username=settings.sftp_user, password=settings.sftp_password.get_secret_value())
        client = paramiko.SFTPClient.from_transport(transport)
        if client is None:
            raise RuntimeError("could not open an SFTP session on the connection")
        for path, remote_name in files:
            if should_stop():
                break
            try:
                client.put(str(path), remote_name)
            except (OSError, paramiko.SSHException) as error:
                on_failure(remote_name, str(error))
                failed.append(remote_name)
                continue
            on_success(path)
    finally:
        transport.close()
    return failed


def check_connection(settings: Settings) -> None:
    """Connect and log in without transferring anything; raises on DNS, network or login problems."""
    settings.require_sftp_credentials()
    transport = paramiko.Transport((settings.sftp_host, settings.sftp_port))
    try:
        transport.connect(username=settings.sftp_user, password=settings.sftp_password.get_secret_value())
    finally:
        transport.close()
