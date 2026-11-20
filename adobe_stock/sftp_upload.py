"""SFTP upload to Adobe Stock. Connection settings come from `adobe_stock.settings`."""

from collections.abc import Callable
from pathlib import Path

import paramiko

from adobe_stock.settings import Settings


def upload_files(
    settings: Settings,
    files: list[tuple[Path, str]],
    on_success: Callable[[Path], None] = lambda _path: None,
) -> list[str]:
    """Upload (local path, remote name) pairs to the account root; return names that failed.

    on_success runs right after each file, so progress survives an interrupted run.
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
            try:
                client.put(str(path), remote_name)
                on_success(path)
                print(f"uploaded {remote_name}")
            except (OSError, paramiko.SSHException) as error:
                print(f"FAILED {remote_name}: {error}")
                failed.append(remote_name)
    finally:
        transport.close()
    return failed
