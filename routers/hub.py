"""JOAO Living OS -- Hub API endpoints.

All /api/* routes for the Hub UI: agents, dispatch, memory, brain, system, logs.
Auth: HUB_SECRET token via query param or Authorization header.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import anthropic
import psutil
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.supabase_client import get_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["hub"])

# -- Auth ------------------------------------------------------------------

HOT_POOL = {"MAX", "CORE", "BYTE"}
ON_DEMAND = {"ARIA", "CJ", "SOFIA", "DEX", "GEMMA", "LEX", "NOVA", "SAGE", "FLUX", "APEX", "IRIS", "VOLT"}
ALL_AGENTS = HOT_POOL | ON_DEMAND

JOAO_SYSTEM_PROMPT = (
    "You are JOAO, Johan Zamora's personal AI exocortex and Chief of Staff. "
    "You manage the Council of 16 AI agents at The Art of The Possible (TAOP). "
    "Johan is CEO. You are direct, energetic, no fluff. You know his projects: "
    "dopamine.watch, dopamine.chat, Dr. Data, TAOP site, the Council infrastructure. "
    "You know his ADHD superpower -- keep responses tight. "
    "You can dispatch agents, check system status, and access JOAO memory. "
    "You remember context within the conversation. "
    "When asked to dispatch, format your response clearly with the agent name and task."
)


def _get_hub_secret() -> str:
    return os.environ.get("HUB_SECRET", "") or os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")


def _check_hub_auth(request: Request, token: str = "") -> None:
    secret = _get_hub_secret()
    if not secret:
        return  # auth disabled if no secret configured

    # Check query param
    if token and hmac.compare_digest(secret, token):
        return

    # Check Authorization header
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:]
        if hmac.compare_digest(secret, bearer_token):
            return

    raise HTTPException(status_code=401, detail="Unauthorized")


# -- Helpers ---------------------------------------------------------------

def _sb():
    """Get Supabase client, raise 503 if unavailable."""
    try:
        return get_client()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Supabase unavailable: {e}")


def _safe_table_op(table: str, op: str, **kwargs) -> Any:
    """Execute a Supabase table operation, return empty on missing table."""
    try:
        sb = _sb()
        query = sb.table(table)
        if op == "select":
            q = query.select(kwargs.get("columns", "*"))
            if kwargs.get("order"):
                q = q.order(kwargs["order"], desc=kwargs.get("desc", True))
            if kwargs.get("limit"):
                q = q.limit(kwargs["limit"])
            if kwargs.get("eq"):
                for k, v in kwargs["eq"].items():
                    q = q.eq(k, v)
            if kwargs.get("is_"):
                for k, v in kwargs["is_"].items():
                    q = q.is_(k, v)
            return q.execute().data or []
        elif op == "insert":
            return query.insert(kwargs["data"]).execute().data
        elif op == "update":
            q = query.update(kwargs["data"])
            if kwargs.get("eq"):
                for k, v in kwargs["eq"].items():
                    q = q.eq(k, v)
            return q.execute().data
    except Exception as e:
        err = str(e)
        if "Could not find the table" in err or "does not exist" in err:
            logger.warning("Table '%s' not found -- run migrations/hub_tables.sql", table)
            return [] if op == "select" else None
        logger.error("Supabase %s on %s failed: %s", op, table, e)
        return [] if op == "select" else None


# -- POST /api/auth --------------------------------------------------------

class AuthRequest(BaseModel):
    token: str


@router.post("/auth")
async def auth(req: AuthRequest):
    secret = _get_hub_secret()
    if not secret:
        return {"status": "ok", "message": "auth disabled"}
    if hmac.compare_digest(secret, req.token):
        return {"status": "ok"}
    raise HTTPException(status_code=401, detail="Invalid token")


# -- GET /api/agents -------------------------------------------------------

@router.get("/agents")
async def agents(request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    result = {}
    for agent in sorted(ALL_AGENTS):
        # pgrep for agent process tree
        try:
            proc = subprocess.run(
                ["pgrep", "-f", f"tmux.*{agent}|claude.*{agent.lower()}"],
                capture_output=True, text=True, timeout=3,
            )
            alive = proc.returncode == 0
        except Exception:
            alive = False

        # Also check tmux session
        try:
            tmux = subprocess.run(
                ["tmux", "has-session", "-t", agent],
                capture_output=True, text=True, timeout=3,
            )
            has_session = tmux.returncode == 0
        except Exception:
            has_session = False

        result[agent] = {
            "alive": alive or has_session,
            "hot_pool": agent in HOT_POOL,
            "session": has_session,
        }

    return {"agents": result, "timestamp": datetime.now(timezone.utc).isoformat()}


# -- POST /api/dispatch ----------------------------------------------------

class DispatchRequest(BaseModel):
    agent: str
    task: str
    project_tag: str = ""


@router.post("/dispatch")
async def dispatch(req: DispatchRequest, request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    agent = req.agent.upper()
    if agent not in ALL_AGENTS:
        raise HTTPException(status_code=422, detail=f"Unknown agent: {agent}")

    dispatch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Write to hub_dispatches
    _safe_table_op("hub_dispatches", "insert", data={
        "id": dispatch_id,
        "agent": agent,
        "task": req.task,
        "project_tag": req.project_tag or None,
        "status": "dispatching",
        "dispatched_at": now,
    })

    # Write to joao_memory
    _safe_table_op("joao_memory", "insert", data={
        "source": "hub_dispatch",
        "content": f"Dispatched {agent}: {req.task}",
        "summary": f"Task dispatched to {agent}",
        "tags": [agent, "dispatch", req.project_tag or "general"],
        "project_ref": req.project_tag or None,
    })

    # Fire actual dispatch via tmux send-keys
    tmux_status = "sent"
    try:
        # Ensure session exists (for on-demand agents)
        subprocess.run(
            ["tmux", "has-session", "-t", agent],
            capture_output=True, timeout=3,
        )
        # Send the task as a command
        escaped_task = req.task.replace("'", "'\\''")
        subprocess.run(
            ["tmux", "send-keys", "-t", agent, f"echo '[HUB DISPATCH] {escaped_task}'", "Enter"],
            capture_output=True, timeout=5,
        )
        tmux_status = "sent"
    except subprocess.TimeoutExpired:
        tmux_status = "timeout"
    except Exception as e:
        tmux_status = f"error: {str(e)[:100]}"
        logger.warning("Dispatch tmux send-keys failed for %s: %s", agent, e)

    # Update dispatch status
    final_status = "dispatched" if tmux_status == "sent" else "failed"
    _safe_table_op("hub_dispatches", "update",
        data={"status": final_status},
        eq={"id": dispatch_id},
    )

    return {
        "dispatch_id": dispatch_id,
        "agent": agent,
        "task": req.task,
        "status": final_status,
        "tmux_status": tmux_status,
        "timestamp": now,
    }


# -- GET /api/output/{agent} -----------------------------------------------

@router.get("/output/{agent}")
async def output(agent: str, request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    agent = agent.upper()
    if agent not in ALL_AGENTS:
        raise HTTPException(status_code=422, detail=f"Unknown agent: {agent}")

    # Capture tmux pane
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", agent, "-p", "-S", "-50"],
            capture_output=True, text=True, timeout=5,
        )
        pane_output = result.stdout.strip() if result.returncode == 0 else ""
    except Exception as e:
        pane_output = f"Error capturing pane: {e}"

    output_hash = hashlib.sha256(pane_output.encode()).hexdigest()[:16]

    # Update latest hub_dispatch for this agent if pending
    dispatches = _safe_table_op("hub_dispatches", "select",
        columns="id,status",
        eq={"agent": agent, "status": "dispatched"},
        order="dispatched_at",
        desc=True,
        limit=1,
    )
    if dispatches:
        _safe_table_op("hub_dispatches", "update",
            data={
                "output": pane_output[:5000],
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
            eq={"id": dispatches[0]["id"]},
        )

    return {
        "agent": agent,
        "output": pane_output,
        "hash": output_hash,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# -- GET /api/dispatches ---------------------------------------------------

@router.get("/dispatches")
async def dispatches(request: Request, token: str = Query(default=""), limit: int = 50):
    _check_hub_auth(request, token)

    rows = _safe_table_op("hub_dispatches", "select",
        order="dispatched_at",
        desc=True,
        limit=min(limit, 100),
    )
    return {"dispatches": rows}


# -- GET /api/system -------------------------------------------------------

@router.get("/system")
async def system(request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    # Check key services
    services = {}
    for svc in ["joao-spine", "cloudflared", "scout"]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=3,
            )
            services[svc] = result.stdout.strip()
        except Exception:
            # Not a systemd service -- check by process
            try:
                proc = subprocess.run(
                    ["pgrep", "-f", svc],
                    capture_output=True, text=True, timeout=3,
                )
                services[svc] = "running" if proc.returncode == 0 else "stopped"
            except Exception:
                services[svc] = "unknown"

    # Uptime
    boot_time = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
    uptime_seconds = (datetime.now(timezone.utc) - boot_time).total_seconds()

    return {
        "cpu_percent": cpu,
        "memory": {
            "total_gb": round(mem.total / (1024**3), 1),
            "used_gb": round(mem.used / (1024**3), 1),
            "percent": mem.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "percent": round(disk.percent, 1),
        },
        "services": services,
        "uptime_seconds": int(uptime_seconds),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# -- GET /api/logs (SSE) ---------------------------------------------------

@router.get("/logs")
async def logs(request: Request, token: str = Query(default=""), lines: int = 100):
    _check_hub_auth(request, token)

    async def stream():
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "-u", "joao-spine", "-f", "-n", str(min(lines, 500)),
            "--no-pager", "-o", "short-iso",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=60)
                if not line:
                    break
                yield f"data: {line.decode('utf-8', errors='replace').rstrip()}\n\n"
        except asyncio.TimeoutError:
            yield "data: [timeout -- reconnect]\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            proc.kill()

    return StreamingResponse(stream(), media_type="text/event-stream")


# -- GET /api/memory -------------------------------------------------------

@router.get("/memory")
async def memory(request: Request, token: str = Query(default=""), limit: int = 30):
    _check_hub_auth(request, token)

    pinned = _safe_table_op("joao_memory", "select",
        order="created_at",
        desc=True,
        limit=20,
        is_={"pinned": "true"},
    )

    recent = _safe_table_op("joao_memory", "select",
        order="created_at",
        desc=True,
        limit=min(limit, 100),
    )

    return {"pinned": pinned, "recent": recent}


# -- POST /api/memory/feed -------------------------------------------------

class MemoryFeedRequest(BaseModel):
    source: str
    content: str
    summary: str = ""
    tags: list[str] = []
    project_ref: str = ""


@router.post("/memory/feed")
async def memory_feed(req: MemoryFeedRequest, request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    row = _safe_table_op("joao_memory", "insert", data={
        "source": req.source,
        "content": req.content,
        "summary": req.summary or req.content[:200],
        "tags": req.tags,
        "project_ref": req.project_ref or None,
    })

    return {"status": "stored", "id": row[0]["id"] if row else None}


# -- PATCH /api/memory/{id}/pin --------------------------------------------

@router.patch("/memory/{memory_id}/pin")
async def memory_pin(memory_id: str, request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    # Get current pin state
    rows = _safe_table_op("joao_memory", "select",
        columns="id,pinned",
        eq={"id": memory_id},
        limit=1,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Memory not found")

    new_state = not rows[0].get("pinned", False)
    _safe_table_op("joao_memory", "update",
        data={"pinned": new_state},
        eq={"id": memory_id},
    )

    return {"id": memory_id, "pinned": new_state}


# -- POST /api/brain -------------------------------------------------------

class BrainRequest(BaseModel):
    messages: list[dict]
    session_id: str = "default"


@router.post("/brain")
async def brain(req: BrainRequest, request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    # Inject context from memory
    context_parts = []

    # Get pinned memories
    pinned = _safe_table_op("joao_memory", "select",
        columns="content,source",
        is_={"pinned": "true"},
        order="created_at",
        desc=True,
        limit=10,
    )
    if pinned:
        context_parts.append("PINNED MEMORIES:\n" + "\n".join(
            f"- [{m['source']}] {m['content'][:200]}" for m in pinned
        ))

    # Get recent dispatches
    recent_dispatches = _safe_table_op("hub_dispatches", "select",
        columns="agent,task,status,dispatched_at",
        order="dispatched_at",
        desc=True,
        limit=5,
    )
    if recent_dispatches:
        context_parts.append("RECENT DISPATCHES:\n" + "\n".join(
            f"- {d['agent']}: {d['task'][:100]} [{d['status']}]" for d in recent_dispatches
        ))

    system_prompt = JOAO_SYSTEM_PROMPT
    if context_parts:
        system_prompt += "\n\nCURRENT CONTEXT:\n" + "\n\n".join(context_parts)

    async def stream():
        client = anthropic.Anthropic()
        full_response = ""
        try:
            with client.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                system=system_prompt,
                messages=req.messages,
            ) as s:
                for text in s.text_stream:
                    full_response += text
                    yield f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'full_text': full_response})}\n\n"

            # Save to joao_memory
            _safe_table_op("joao_memory", "insert", data={
                "source": "brain_chat",
                "content": f"User: {req.messages[-1].get('content', '')[:300]}\nJOAO: {full_response[:500]}",
                "summary": full_response[:200],
                "tags": ["brain", "chat"],
            })

            # Save session
            _safe_table_op("joao_sessions", "insert", data={
                "messages": req.messages + [{"role": "assistant", "content": full_response}],
                "summary": full_response[:200],
            })

        except Exception as e:
            logger.exception("Brain streaming error")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# -- POST /api/scout/summarize ---------------------------------------------

@router.post("/scout/summarize")
async def scout_summarize(request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    # Get unsummarized memories
    unsummarized = _safe_table_op("joao_memory", "select",
        columns="id,content,source,tags",
        is_={"summarized": "false"},
        order="created_at",
        desc=True,
        limit=50,
    )

    if not unsummarized:
        return {"status": "nothing_to_summarize", "count": 0}

    # Batch summarize with Claude
    content_block = "\n\n".join(
        f"[{m['source']}] {m['content'][:500]}" for m in unsummarized
    )

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system="You are a memory summarizer. Given a batch of memories, create a concise summary of key themes, decisions, and action items. Be direct.",
        messages=[{"role": "user", "content": f"Summarize these {len(unsummarized)} memories:\n\n{content_block}"}],
    )
    summary = msg.content[0].text

    # Mark as summarized
    for m in unsummarized:
        _safe_table_op("joao_memory", "update",
            data={"summarized": True, "summary": summary[:500]},
            eq={"id": m["id"]},
        )

    # Store the nightly summary as a new memory
    _safe_table_op("joao_memory", "insert", data={
        "source": "scout_nightly",
        "content": summary,
        "summary": f"Nightly summary of {len(unsummarized)} memories",
        "tags": ["scout", "nightly", "summary"],
        "pinned": True,
    })

    return {"status": "summarized", "count": len(unsummarized), "summary": summary}


# -- POST /api/service/restart/{service} -----------------------------------

@router.post("/service/restart/{service}")
async def service_restart(service: str, request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    allowed = {"scout", "cloudflared", "joao-spine"}
    if service not in allowed:
        raise HTTPException(status_code=422, detail=f"Cannot restart '{service}'. Allowed: {sorted(allowed)}")

    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", service],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {"status": "restarted", "service": service}
        return {"status": "failed", "service": service, "error": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "service": service}
    except Exception as e:
        return {"status": "error", "service": service, "error": str(e)}


# -- GET /api/agent-output/{agent} ----------------------------------------

@router.get("/agent-output/{agent}")
async def agent_output(agent: str, request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    agent = agent.upper()
    if agent not in ALL_AGENTS:
        raise HTTPException(status_code=422, detail=f"Unknown agent: {agent}")

    lines = []
    source = "none"

    # Try output files first
    try:
        result = subprocess.run(
            ["bash", "-c", f"ls -t /tmp/council/outputs/{agent}_*.md 2>/dev/null | head -1"],
            capture_output=True, text=True, timeout=3,
        )
        output_file = result.stdout.strip()
        if output_file:
            tail = subprocess.run(
                ["tail", "-30", output_file],
                capture_output=True, text=True, timeout=3,
            )
            if tail.returncode == 0 and tail.stdout.strip():
                lines = tail.stdout.strip().split("\n")
                source = "file"
    except Exception as e:
        logger.warning("agent-output file read failed for %s: %s", agent, e)

    # Also try tmux pane capture
    tmux_lines = []
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", agent, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            tmux_lines = [l for l in result.stdout.strip().split("\n") if l.strip()][-20:]
            if not lines:
                lines = tmux_lines
                source = "tmux"
    except Exception as e:
        logger.debug("tmux capture for %s unavailable: %s", agent, e)

    return {
        "agent": agent,
        "source": source,
        "lines": lines,
        "tmux_lines": tmux_lines,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# -- GET /api/credits ------------------------------------------------------

@router.get("/credits")
async def credits(request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    balance = None
    key_label = "JOAO-Primary"
    note = None

    # Try reading CREDIT_BALANCE from env
    env_balance = os.environ.get("CREDIT_BALANCE")
    if env_balance:
        try:
            balance = float(env_balance)
        except ValueError:
            pass

    # Try session count from Supabase for estimation
    session_count = 0
    try:
        rows = _safe_table_op("joao_sessions", "select", columns="id", limit=1000)
        session_count = len(rows) if rows else 0
    except Exception:
        pass

    if balance is None:
        note = "manual update required"

    return {
        "balance": balance,
        "key_label": key_label,
        "session_count": session_count,
        "note": note,
        "updated": datetime.now(timezone.utc).isoformat(),
    }


# -- GET /api/services -----------------------------------------------------

@router.get("/services")
async def services(request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    svc_list = [
        {"name": "cloudflared", "check": "systemctl is-active cloudflared", "url": "joao.theartofthepossible.io", "port": 443},
        {"name": "joao-spine", "check": "pgrep -f 'uvicorn.*7778'", "url": "localhost:7778", "port": 7778},
        {"name": "scout-monitor", "check": "systemctl is-active scout-monitor", "url": None, "port": None},
        {"name": "dispatch-listener", "check": "pgrep -f '8100'", "url": "localhost:8100", "port": 8100},
    ]

    results = []
    for svc in svc_list:
        try:
            proc = subprocess.run(
                ["bash", "-c", svc["check"]],
                capture_output=True, text=True, timeout=5,
            )
            status = "alive" if proc.returncode == 0 else "dead"
        except Exception:
            status = "unknown"

        results.append({
            "name": svc["name"],
            "status": status,
            "url": svc["url"],
            "port": svc["port"],
        })

    return {"services": results, "timestamp": datetime.now(timezone.utc).isoformat()}


# -- GET /api/projects -----------------------------------------------------

import httpx as _httpx

@router.get("/projects")
async def projects(request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    project_list = [
        {"name": "dopamine.watch", "category": "STREAMING", "url": "https://app.dopamine.watch", "tagline": "Feel-First streaming prescription"},
        {"name": "Dr. Data", "category": "ENTERPRISE", "url": "https://drdata.theartofthepossible.io", "tagline": "AI Tableau to Power BI migration"},
        {"name": "FocusFlow", "category": "PRODUCTIVITY", "url": "https://focusflow.theartofthepossible.io", "tagline": "ADHD summarizer, 6 output formats"},
        {"name": "Arena", "category": "AI RESEARCH", "url": "https://joao.theartofthepossible.io/arena", "tagline": "7-brain parallel AI debate"},
        {"name": "JOAO Hub", "category": "LIVING OS", "url": "https://joao.theartofthepossible.io/hub", "tagline": "AI exocortex, 16-agent Council"},
        {"name": "dopamine.chat", "category": "MESSAGING", "url": "https://dopamine.chat", "tagline": "Privacy-first, no shame mechanics"},
    ]

    results = []
    async with _httpx.AsyncClient(timeout=3.0, verify=False) as client:
        for proj in project_list:
            status = "unknown"
            try:
                resp = await client.head(proj["url"])
                status = "alive" if resp.status_code < 500 else "dead"
            except Exception:
                status = "dead"
            results.append({**proj, "status": status})

    return {"projects": results, "timestamp": datetime.now(timezone.utc).isoformat()}
