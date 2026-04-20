"""Codex CLI brain — shells out to `codex exec` for headless OpenAI Codex tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

logger = logging.getLogger("joao.codex_brain")

CODEX_BIN = os.getenv("CODEX_BIN", "/home/zamoritacr/.npm-global/bin/codex")
DEFAULT_MODEL = "gpt-4o"  # Swap to gpt-5.4-codex when available
DEFAULT_TIMEOUT = 90


async def codex_ask(
    prompt: str,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run a prompt through codex exec --json and return parsed result.

    Returns dict with: response_text, token_usage, model, elapsed_ms, raw_events
    """
    env = {**os.environ, "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "")}
    # Source ~/.env if key not already in env
    if not env.get("OPENAI_API_KEY"):
        env_file = os.path.expanduser("~/.env")
        if os.path.isfile(env_file):
            with open(env_file) as f:
                for line in f:
                    if line.startswith("OPENAI_API_KEY="):
                        env["OPENAI_API_KEY"] = line.strip().split("=", 1)[1]
                        break

    cmd = [
        CODEX_BIN, "exec",
        "--json",
        "--ephemeral",
        "-c", f'model="{model}"',
        prompt,
    ]

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        elapsed = int((time.monotonic() - t0) * 1000)
        return {
            "response_text": "",
            "token_usage": {},
            "model": model,
            "elapsed_ms": elapsed,
            "error": f"Timeout after {timeout}s",
            "raw_events": [],
        }
    except FileNotFoundError:
        return {
            "response_text": "",
            "token_usage": {},
            "model": model,
            "elapsed_ms": 0,
            "error": f"Codex binary not found at {CODEX_BIN}",
            "raw_events": [],
        }

    elapsed = int((time.monotonic() - t0) * 1000)
    raw = stdout.decode("utf-8", errors="replace")

    # Parse JSONL events
    events = []
    response_text = ""
    token_usage = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
            events.append(evt)
            # Extract response text from item.completed events
            if evt.get("type") == "item.completed":
                item = evt.get("item", {})
                if item.get("text"):
                    response_text = item["text"]
            # Extract token usage from turn.completed events
            if evt.get("type") == "turn.completed":
                token_usage = evt.get("usage", {})
        except json.JSONDecodeError:
            events.append({"type": "raw", "data": line})

    if proc.returncode != 0 and not response_text:
        err_text = stderr.decode("utf-8", errors="replace").strip()
        return {
            "response_text": "",
            "token_usage": token_usage,
            "model": model,
            "elapsed_ms": elapsed,
            "error": err_text or f"codex exited with code {proc.returncode}",
            "raw_events": events,
        }

    return {
        "response_text": response_text,
        "token_usage": token_usage,
        "model": model,
        "elapsed_ms": elapsed,
        "raw_events": events,
    }
