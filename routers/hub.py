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
from pathlib import Path
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
    "Do not claim you lack access when hub APIs and tmux are reachable; verify via tools first. "
    "If a capability is down, report exactly which endpoint failed and why. "
    "You remember context within the conversation. "
    "When asked to dispatch, format your response clearly with the agent name and task."
)

JOAO_SKILL_STACK_PROMPT = (
    "\n\nOPERATING POLICY (MANDATORY):\n"
    "- JOAO runs with full capability inheritance across V1 + V2 + V3 stacks.\n"
    "- Prefer strongest available skill path, not minimal/demo behavior.\n"
    "- For coding requests: inspect -> modify -> verify -> report evidence.\n"
    "- For infrastructure requests: provide concrete endpoint/process evidence.\n"
    "- Never roleplay execution. Either show real evidence or name the failing dependency.\n"
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
    mode: str = "joao"


# Load MrDP system prompt once at import, reload on each request for dev convenience
_MRDP_PROMPT_PATH = Path(__file__).parent.parent / "mrdp_system_prompt.md"
_DRDATA_PROMPT_PATH = Path(__file__).parent.parent / "drdata_system_prompt.md"


def _load_mrdp_prompt() -> str:
    try:
        return _MRDP_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "You are MrDP, a neurodivergent life companion built from neuroscience."


def _load_drdata_prompt() -> str:
    try:
        return _DRDATA_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            "You are Dr. Data, TAOP's elite data intelligence operator. "
            "You specialize in BI migration, Tableau to Power BI translation, "
            "data quality diagnostics, and executive analytics narratives."
        )


def _extract_last_user_text(messages: list[dict]) -> str:
    if not messages:
        return ""
    raw = messages[-1].get("content", "")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return str(raw)


def _is_truth_probe_query(text: str) -> bool:
    t = text.lower()
    probes = [
        "access now",
        "dispatch working",
        "prove it",
        "are you connected",
        "are you live",
        "do you have access",
        "status",
    ]
    return any(p in t for p in probes)


def _build_truth_report() -> str:
    now = datetime.now(timezone.utc).isoformat()

    # Core process checks
    try:
        spine_proc = subprocess.run(
            ["pgrep", "-f", r"python3 -m uvicorn main:app --host 0.0.0.0 --port 7778"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        spine_alive = spine_proc.returncode == 0
    except Exception:
        spine_alive = False

    try:
        dispatch_probe = subprocess.run(
            ["curl", "-sS", "-m", "3", "http://127.0.0.1:8100/health"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        dispatch_alive = dispatch_probe.returncode == 0 and "alive" in (dispatch_probe.stdout or "")
    except Exception:
        dispatch_alive = False

    # ARIA truth
    try:
        aria_tmux = subprocess.run(
            ["tmux", "has-session", "-t", "ARIA"],
            capture_output=True,
            timeout=3,
        )
        aria_session = aria_tmux.returncode == 0
    except Exception:
        aria_session = False

    try:
        exec_probe = subprocess.run(
            ["bash", "-lc", "whoami && hostname && pwd"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        exec_ok = exec_probe.returncode == 0
        exec_out = (exec_probe.stdout or "").strip()
    except Exception as exc:
        exec_ok = False
        exec_out = str(exc)[:180]

    latest_dispatch = _safe_table_op(
        "hub_dispatches",
        "select",
        columns="id,agent,status,dispatched_at,task",
        order="dispatched_at",
        desc=True,
        limit=1,
    )
    d = latest_dispatch[0] if latest_dispatch else {}

    lines = [
        "Live JOAO truth report (not simulated):",
        f"- timestamp: {now}",
        f"- joao_spine: {'live' if spine_alive else 'down'}",
        f"- dispatch_service: {'live' if dispatch_alive else 'down'}",
        f"- aria_tmux_session: {'present' if aria_session else 'missing'}",
        f"- shell_exec_proof: {'ok' if exec_ok else 'failed'} :: {exec_out}",
        f"- latest_dispatch: {d.get('agent', 'none')} :: {d.get('status', 'n/a')} :: {str(d.get('task', ''))[:80]}",
    ]
    return "\n".join(lines)


@router.post("/brain")
async def brain(req: BrainRequest, request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    is_mrdp = req.mode == "mrdp"
    is_drdata = req.mode == "drdata"
    last_user_text = _extract_last_user_text(req.messages)

    # Hard guardrail for access/proof/status questions: return real runtime evidence.
    if not is_mrdp and not is_drdata and _is_truth_probe_query(last_user_text):
        report = _build_truth_report()

        async def truth_stream():
            yield f"data: {json.dumps({'type': 'token', 'text': report})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'full_text': report})}\n\n"

        _safe_table_op("joao_memory", "insert", data={
            "source": "brain_truth_probe",
            "content": f"User: {last_user_text[:220]}\nJOAO: {report[:1200]}",
            "summary": "Live truth probe response generated from runtime checks.",
            "tags": ["brain", "truth_probe", "status"],
        })
        return StreamingResponse(truth_stream(), media_type="text/event-stream")

    if is_mrdp:
        system_prompt = _load_mrdp_prompt() + JOAO_SKILL_STACK_PROMPT
        model = "claude-opus-4-6"
    elif is_drdata:
        system_prompt = (
            _load_drdata_prompt()
            + "\n\nYou have priority access to Dr. Data workflows and should reason like a senior analytics architect."
            + JOAO_SKILL_STACK_PROMPT
        )
        model = "claude-sonnet-4-20250514"
    else:
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

        system_prompt = JOAO_SYSTEM_PROMPT + JOAO_SKILL_STACK_PROMPT
        if context_parts:
            system_prompt += "\n\nCURRENT CONTEXT:\n" + "\n\n".join(context_parts)
        model = "claude-sonnet-4-20250514"

    async def stream():
        client = anthropic.Anthropic()
        full_response = ""
        try:
            with client.messages.stream(
                model=model,
                max_tokens=2048,
                system=system_prompt,
                messages=req.messages,
            ) as s:
                for text in s.text_stream:
                    full_response += text
                    yield f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'full_text': full_response})}\n\n"

            # Save to joao_memory
            source_tag = "mrdp_chat" if is_mrdp else "brain_chat"
            _safe_table_op("joao_memory", "insert", data={
                "source": source_tag,
                "content": f"User: {req.messages[-1].get('content', '')[:300]}\n{'MrDP' if is_mrdp else 'JOAO'}: {full_response[:500]}",
                "summary": full_response[:200],
                "tags": [source_tag, "chat"],
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


# -- GET /api/build-log ---------------------------------------------------

@router.get("/build-log")
async def build_log(request: Request, token: str = Query(default=""), limit: int = 50):
    _check_hub_auth(request, token)

    rows = _safe_table_op("build_log", "select",
        order="created_at",
        desc=True,
        limit=min(limit, 200),
    )
    return {"entries": rows}


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

# -- GET /api/provider-health -------------------------------------------------

@router.get("/provider-health")
async def provider_health(request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    def _http_probe(url: str, headers: dict[str, str] | None = None, timeout: int = 5) -> dict[str, Any]:
        try:
            import httpx
            with httpx.Client(timeout=timeout, verify=False) as client:
                response = client.get(url, headers=headers or {})
                return {
                    "ok": response.status_code < 500,
                    "status_code": response.status_code,
                }
        except Exception as exc:
            return {"ok": False, "status_code": 0, "error": str(exc)[:180]}

    providers: dict[str, Any] = {}

    # JOAO core services
    # Avoid self-HTTP probe for 7778 from within the same request path; use process check.
    try:
        spine_proc = subprocess.run(
            ["pgrep", "-f", r"python3 -m uvicorn main:app --host 0.0.0.0 --port 7778"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        spine_ok = spine_proc.returncode == 0
    except Exception:
        spine_ok = False

    dispatch = _http_probe("http://127.0.0.1:8100/health")
    providers["joao_spine"] = {
        "status": "live" if spine_ok else "down",
        "note": "Core exocortex API service",
        "detail": "process check",
    }
    providers["dispatch"] = {
        "status": "live" if dispatch.get("ok") else "down",
        "note": "Task dispatch service",
        "detail": f"HTTP {dispatch.get('status_code', 0)}",
    }

    # Cloudflare API
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    cf_headers = {"Authorization": f"Bearer {cf_token}"} if cf_token else {}
    cf_probe = _http_probe("https://api.cloudflare.com/client/v4/accounts", headers=cf_headers)
    providers["cloudflare"] = {
        "status": "configured" if cf_probe.get("ok") and cf_probe.get("status_code") == 200 else ("missing-token" if not cf_token else "auth-error"),
        "note": "DNS, tunnel, edge, WAF, and R2 control plane",
        "detail": f"HTTP {cf_probe.get('status_code', 0)}",
    }

    # Supabase
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if sb_url and sb_service_key:
        sb_probe = _http_probe(
            f"{sb_url.rstrip('/')}/rest/v1/",
            headers={
                "apikey": sb_service_key,
                "Authorization": f"Bearer {sb_service_key}",
            },
        )
        sb_status = "configured" if sb_probe.get("status_code") == 200 else "auth-error"
        sb_detail = f"HTTP {sb_probe.get('status_code', 0)}"
    else:
        sb_status = "missing-token"
        sb_detail = "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing"
    providers["supabase"] = {
        "status": sb_status,
        "note": "Memory/session data plane",
        "detail": sb_detail,
    }

    # Neon
    neon_rest = os.environ.get("NEON_REST_URL", "").strip()
    neon_db = os.environ.get("NEON_DATABASE_URL", "").strip()
    neon_rest_probe = _http_probe(neon_rest) if neon_rest else {"ok": False, "status_code": 0}
    neon_psql_ok = False
    neon_psql_detail = "psql check skipped"
    if neon_db:
        try:
            p = subprocess.run(
                ["psql", neon_db, "-c", "select 1;", "-t", "-A"],
                capture_output=True,
                text=True,
                timeout=6,
            )
            neon_psql_ok = p.returncode == 0
            neon_psql_detail = "psql ok" if neon_psql_ok else (p.stderr.strip()[:160] or "psql failed")
        except Exception as exc:
            neon_psql_detail = str(exc)[:160]
    neon_ok = neon_psql_ok or neon_rest_probe.get("ok", False)
    providers["neon"] = {
        "status": "configured" if neon_ok else "missing-token",
        "note": "Postgres production data plane",
        "detail": f"REST HTTP {neon_rest_probe.get('status_code', 0)} · {neon_psql_detail}",
    }

    # GitHub
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    gh_probe = _http_probe(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {gh_token}"} if gh_token else {},
    )
    providers["github"] = {
        "status": "configured" if gh_probe.get("status_code") == 200 else ("missing-token" if not gh_token else "auth-error"),
        "note": "Source control and CI/CD trigger surface",
        "detail": f"HTTP {gh_probe.get('status_code', 0)}",
    }

    # Dr. Data availability (deterministic local checks, avoid self HTTP recursion)
    drdata_v2_index = Path.home() / "taop" / "drdata-v2" / "index.html"
    drdata_v1_proc = subprocess.run(
        ["pgrep", "-f", "drdata|streamlit.*8502|8503|8504"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    drdata_live = drdata_v2_index.exists() or drdata_v1_proc.returncode == 0
    drdata_detail = []
    drdata_detail.append("v2 index present" if drdata_v2_index.exists() else "v2 index missing")
    drdata_detail.append("v1/v2 process up" if drdata_v1_proc.returncode == 0 else "no drdata process")
    providers["drdata"] = {
        "status": "live" if drdata_live else "down",
        "note": "Dr. Data V1/V2 intelligence surface",
        "detail": " · ".join(drdata_detail),
    }

    return {
        "providers": providers,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# -- GET /api/exec-proof -----------------------------------------------------

@router.get("/exec-proof")
async def exec_proof(request: Request, token: str = Query(default="")):
    _check_hub_auth(request, token)

    now = datetime.now(timezone.utc).isoformat()
    proof_file = Path("/tmp/joao_exec_proof.txt")
    command = "whoami && hostname && pwd"

    try:
        result = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        proof_line = f"{now} :: {output}\n"
        proof_file.write_text(proof_line, encoding="utf-8")

        return {
            "ok": result.returncode == 0,
            "command": command,
            "returncode": result.returncode,
            "output": output,
            "error": error[:300],
            "proof_file": str(proof_file),
            "timestamp": now,
        }
    except Exception as exc:
        return {
            "ok": False,
            "command": command,
            "returncode": -1,
            "output": "",
            "error": str(exc)[:300],
            "proof_file": str(proof_file),
            "timestamp": now,
        }
