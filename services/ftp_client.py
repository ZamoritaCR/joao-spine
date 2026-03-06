"""FTP client service — list, get, put, delete files on remote FTP servers."""

from __future__ import annotations

import asyncio
import ftplib
import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _connect(host: str, port: int, user: str, password: str) -> ftplib.FTP:
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=15)
    ftp.login(user, password)
    return ftp


async def ftp_list(
    host: str, port: int, user: str, password: str, remote_path: str
) -> list[dict]:
    """List files/dirs at remote_path."""

    def _list():
        ftp = _connect(host, port, user, password)
        try:
            entries: list[dict] = []
            ftp.cwd(remote_path)
            lines: list[str] = []
            ftp.retrlines("LIST", lines.append)
            for line in lines:
                parts = line.split(None, 8)
                if len(parts) >= 9:
                    entries.append(
                        {
                            "name": parts[8],
                            "size": parts[4],
                            "type": "dir" if line.startswith("d") else "file",
                            "raw": line,
                        }
                    )
                else:
                    entries.append({"name": line.strip(), "raw": line})
            return entries
        finally:
            ftp.quit()

    return await asyncio.to_thread(_list)


async def ftp_get(
    host: str, port: int, user: str, password: str, remote_path: str, local_path: str
) -> str:
    """Download remote_path to local_path. Returns bytes written."""

    def _get():
        ftp = _connect(host, port, user, password)
        try:
            dest = Path(local_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                ftp.retrbinary(f"RETR {remote_path}", f.write)
            return dest.stat().st_size
        finally:
            ftp.quit()

    size = await asyncio.to_thread(_get)
    return f"Downloaded {remote_path} -> {local_path} ({size} bytes)"


async def ftp_put(
    host: str, port: int, user: str, password: str, local_path: str, remote_path: str
) -> str:
    """Upload local_path to remote_path."""

    def _put():
        ftp = _connect(host, port, user, password)
        try:
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {remote_path}", f)
            return Path(local_path).stat().st_size
        finally:
            ftp.quit()

    size = await asyncio.to_thread(_put)
    return f"Uploaded {local_path} -> {remote_path} ({size} bytes)"


async def ftp_delete(
    host: str, port: int, user: str, password: str, remote_path: str
) -> str:
    """Delete a file at remote_path."""

    def _delete():
        ftp = _connect(host, port, user, password)
        try:
            ftp.delete(remote_path)
        finally:
            ftp.quit()

    await asyncio.to_thread(_delete)
    return f"Deleted {remote_path}"
