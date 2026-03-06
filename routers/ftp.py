"""FTP operations endpoint for JOAO spine."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import ftp_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/joao", tags=["ftp"])


class FTPRequest(BaseModel):
    action: Literal["list", "get", "put", "delete"]
    host: str
    port: int = Field(default=21)
    user: str
    password: str
    remote_path: str
    local_path: str | None = None


@router.post("/ftp")
async def ftp_operation(req: FTPRequest):
    """Execute an FTP operation (list/get/put/delete)."""
    logger.info("FTP %s on %s:%d path=%s", req.action, req.host, req.port, req.remote_path)

    try:
        if req.action == "list":
            entries = await ftp_client.ftp_list(
                req.host, req.port, req.user, req.password, req.remote_path
            )
            return {"status": "ok", "action": "list", "entries": entries}

        elif req.action == "get":
            if not req.local_path:
                raise HTTPException(400, "local_path required for get action")
            result = await ftp_client.ftp_get(
                req.host, req.port, req.user, req.password, req.remote_path, req.local_path
            )
            return {"status": "ok", "action": "get", "result": result}

        elif req.action == "put":
            if not req.local_path:
                raise HTTPException(400, "local_path required for put action")
            result = await ftp_client.ftp_put(
                req.host, req.port, req.user, req.password, req.local_path, req.remote_path
            )
            return {"status": "ok", "action": "put", "result": result}

        elif req.action == "delete":
            result = await ftp_client.ftp_delete(
                req.host, req.port, req.user, req.password, req.remote_path
            )
            return {"status": "ok", "action": "delete", "result": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FTP operation failed")
        raise HTTPException(500, f"FTP {req.action} failed: {e}")
