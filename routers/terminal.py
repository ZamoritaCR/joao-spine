"""Browser terminal -- WebSocket pty bridge + session management endpoints."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from terminal_manager import terminal_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["terminal"])

PTY_READ_INTERVAL = 0.02  # 50Hz poll


def _check_token(token: str | None) -> None:
    """Validate terminal access token. Raises HTTPException on failure."""
    expected = os.environ.get("JOAO_TERMINAL_TOKEN", "")
    if not expected:
        # Fallback: use dispatch HMAC secret as token
        expected = os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")
    if not expected:
        logger.warning("No JOAO_TERMINAL_TOKEN or JOAO_DISPATCH_HMAC_SECRET set -- terminal auth disabled")
        return
    if not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.websocket("/ws/terminal")
async def terminal_ws(
    ws: WebSocket,
    session_id: str = Query(default="default"),
    cols: int = Query(default=80),
    rows: int = Query(default=24),
    token: str = Query(default=""),
):
    """
    WebSocket protocol:
    Client -> Server:
      {"type": "input", "data": "..."}       -- keystrokes
      {"type": "resize", "cols": N, "rows": N}
      {"type": "ping"}
    Server -> Client:
      {"type": "output", "data": "..."}       -- pty output
      {"type": "scrollback", "data": "..."}   -- reconnect buffer
      {"type": "exit", "code": N}             -- pty exited
      {"type": "pong"}
      {"type": "error", "message": "..."}
    """
    # Auth check
    expected = os.environ.get("JOAO_TERMINAL_TOKEN", "") or os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")
    if expected and (not token or not hmac.compare_digest(expected, token)):
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    logger.info("Terminal WebSocket connected: session=%s", session_id)

    # Create or reconnect
    session = terminal_manager.get_session(session_id)
    reconnecting = session is not None

    if not session:
        session = terminal_manager.create_session(session_id, cols, rows)
    else:
        terminal_manager.resize_session(session_id, cols, rows)

    # Send scrollback on reconnect
    if reconnecting and session.scrollback:
        try:
            await ws.send_json({
                "type": "scrollback",
                "data": session.scrollback.decode("utf-8", errors="replace"),
            })
        except Exception:
            pass

    # Bidirectional bridge
    async def pty_reader():
        """Read pty output and send to WebSocket."""
        while True:
            if not session.pty.isalive():
                try:
                    exit_code = session.pty.exitstatus or 0
                    await ws.send_json({"type": "exit", "code": exit_code})
                except Exception:
                    pass
                return
            data = terminal_manager.read_output(session)
            if data:
                try:
                    await ws.send_json({
                        "type": "output",
                        "data": data.decode("utf-8", errors="replace"),
                    })
                except Exception:
                    return
            else:
                await asyncio.sleep(PTY_READ_INTERVAL)

    async def ws_reader():
        """Read WebSocket input and write to pty."""
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                return

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "input":
                data = msg.get("data", "")
                if data and session.pty.isalive():
                    terminal_manager.write_input(session, data)

            elif msg_type == "resize":
                c = msg.get("cols", 80)
                r = msg.get("rows", 24)
                terminal_manager.resize_session(session_id, c, r)

            elif msg_type == "ping":
                try:
                    await ws.send_json({"type": "pong"})
                except Exception:
                    return

    reader_task = asyncio.create_task(pty_reader())
    writer_task = asyncio.create_task(ws_reader())

    try:
        done, pending = await asyncio.wait(
            [reader_task, writer_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except Exception:
        reader_task.cancel()
        writer_task.cancel()

    logger.info("Terminal WebSocket disconnected: session=%s", session_id)


@router.get("/api/terminal/sessions")
async def list_sessions(token: str = Query(default="")):
    _check_token(token)
    return {"sessions": terminal_manager.list_sessions()}


@router.post("/api/terminal/kill")
async def kill_session(session_id: str = Query(...), token: str = Query(default="")):
    _check_token(token)
    killed = terminal_manager.kill_session(session_id)
    if not killed:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "killed", "session_id": session_id}
