"""PTY session manager for browser-based terminal access."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import logging
import os
import struct
import termios
import time
from dataclasses import dataclass, field

import ptyprocess

logger = logging.getLogger(__name__)

MAX_SCROLLBACK = 50 * 1024  # 50KB
IDLE_TIMEOUT = 30 * 60  # 30 minutes
CLEANUP_INTERVAL = 60  # check every 60s


@dataclass
class TerminalSession:
    session_id: str
    pty: ptyprocess.PtyProcess
    scrollback: bytearray = field(default_factory=bytearray)
    last_active: float = field(default_factory=time.time)
    cols: int = 80
    rows: int = 24

    def touch(self) -> None:
        self.last_active = time.time()

    def append_scrollback(self, data: bytes) -> None:
        self.scrollback.extend(data)
        if len(self.scrollback) > MAX_SCROLLBACK:
            self.scrollback = self.scrollback[-MAX_SCROLLBACK:]


class TerminalManager:
    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("TerminalManager started")

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        for sid in list(self._sessions):
            self.kill_session(sid)
        logger.info("TerminalManager stopped, all sessions killed")

    def create_session(
        self, session_id: str, cols: int = 80, rows: int = 24
    ) -> TerminalSession:
        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.touch()
            self.resize_session(session_id, cols, rows)
            return session

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["LANG"] = "en_US.UTF-8"

        shell = os.environ.get("SHELL", "/bin/bash")
        pty = ptyprocess.PtyProcess.spawn(
            [shell, "--login"],
            dimensions=(rows, cols),
            env=env,
            cwd=os.path.expanduser("~"),
        )

        # Set non-blocking reads
        fd = pty.fd
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        session = TerminalSession(
            session_id=session_id,
            pty=pty,
            cols=cols,
            rows=rows,
        )
        self._sessions[session_id] = session
        logger.info("Terminal session created: %s (%dx%d)", session_id, cols, rows)
        return session

    def get_session(self, session_id: str) -> TerminalSession | None:
        session = self._sessions.get(session_id)
        if session and not session.pty.isalive():
            logger.info("Session %s pty is dead, cleaning up", session_id)
            self.kill_session(session_id)
            return None
        return session

    def kill_session(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if not session:
            return False
        try:
            if session.pty.isalive():
                session.pty.terminate(force=True)
        except Exception:
            pass
        logger.info("Terminal session killed: %s", session_id)
        return True

    def resize_session(self, session_id: str, cols: int, rows: int) -> bool:
        session = self._sessions.get(session_id)
        if not session or not session.pty.isalive():
            return False
        try:
            session.pty.setwinsize(rows, cols)
            session.cols = cols
            session.rows = rows
        except Exception as e:
            logger.warning("Resize failed for %s: %s", session_id, e)
            return False
        return True

    def read_output(self, session: TerminalSession, max_bytes: int = 4096) -> bytes:
        """Non-blocking read from pty. Returns empty bytes if nothing available."""
        try:
            data = os.read(session.pty.fd, max_bytes)
            session.append_scrollback(data)
            session.touch()
            return data
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return b""
            raise

    def write_input(self, session: TerminalSession, data: str) -> None:
        session.pty.write(data.encode("utf-8", errors="replace"))
        session.touch()

    def list_sessions(self) -> list[dict]:
        result = []
        for sid, session in self._sessions.items():
            result.append({
                "session_id": sid,
                "alive": session.pty.isalive(),
                "cols": session.cols,
                "rows": session.rows,
                "idle_seconds": int(time.time() - session.last_active),
                "scrollback_bytes": len(session.scrollback),
            })
        return result

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            now = time.time()
            stale = [
                sid
                for sid, s in self._sessions.items()
                if (now - s.last_active > IDLE_TIMEOUT) or not s.pty.isalive()
            ]
            for sid in stale:
                logger.info("Cleaning up idle/dead session: %s", sid)
                self.kill_session(sid)


terminal_manager = TerminalManager()
