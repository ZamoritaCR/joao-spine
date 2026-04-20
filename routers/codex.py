"""Codex brain router — POST /joao/codex/ask for headless Codex dispatch."""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.codex_brain import codex_ask

logger = logging.getLogger("joao.codex_router")

router = APIRouter(prefix="/joao/codex", tags=["codex"])


def _check_auth(request: Request) -> None:
    secret = os.environ.get("JOAO_DISPATCH_SECRET", "") or os.environ.get("HUB_SECRET", "")
    if not secret:
        return  # auth disabled if no secret configured
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if hmac.compare_digest(secret, token):
            return
    raise HTTPException(status_code=401, detail="Unauthorized")


class CodexRequest(BaseModel):
    prompt: str
    model: str = "gpt-4o"
    timeout: int = 90


class CodexResponse(BaseModel):
    response_text: str
    token_usage: dict
    model: str
    elapsed_ms: int
    error: str | None = None


@router.post("/ask", response_model=CodexResponse)
async def ask_codex(req: CodexRequest, request: Request):
    _check_auth(request)
    result = await codex_ask(prompt=req.prompt, model=req.model, timeout=req.timeout)
    return CodexResponse(
        response_text=result["response_text"],
        token_usage=result["token_usage"],
        model=result["model"],
        elapsed_ms=result["elapsed_ms"],
        error=result.get("error"),
    )
