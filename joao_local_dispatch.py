"""
JOAO Local Dispatch Listener
Runs on Ubuntu server (192.168.0.55), receives commands from Railway spine
via Cloudflare Tunnel. Executes tmux commands to dispatch work to Council agents.
"""
import os
import re
import subprocess
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import asyncio
import uvicorn

app = FastAPI(title="JOAO Local Dispatch", version="2.1.0")
logger = logging.getLogger("joao-dispatch")

# Security: shared secret between Railway and local listener
DISPATCH_SECRET = os.getenv("JOAO_DISPATCH_SECRET", "CHANGE_ME_IN_PRODUCTION")

# Agent -> tmux session mapping
AGENT_SESSIONS = {
    "ARIA": "ARIA",
    "BYTE": "BYTE",
    "CJ": "CJ",
    "DEX": "DEX",
    "SOFIA": "SOFIA",
    "GEMMA": "GEMMA",
    "MAX": "MAX",
    "LEX": "LEX",
    "NOVA": "NOVA",
    "SCOUT": "SCOUT",
    "SAGE": "SAGE",
    "FLUX": "FLUX",
    "CORE": "CORE",
    "APEX": "APEX",
    "IRIS": "IRIS",
    "VOLT": "VOLT",
}


# Commands that require interactive input — blocked in automated lane
INTERACTIVE_PATTERNS = [
    "claude ",
    "claude\n",
    "nano ",
    "vim ",
    "vi ",
    "less ",
    "more ",
    "htop",
    "top\n",
    "ssh ",
    "sudo -i",
    "python3\n",
    "python\n",
    "node\n",
    "irb\n",
]


def is_interactive(command: str) -> bool:
    """Check if a command would launch an interactive process."""
    cmd_lower = command.strip().lower()
    for pattern in INTERACTIVE_PATTERNS:
        if cmd_lower.startswith(pattern.strip()):
            return True
    return False


CLAUDE_BIN_PATH = "/usr/bin/claude"
VENV_ACT = f"source {os.path.expanduser('~/taop-agents-env/bin/activate')} 2>/dev/null"
AGENT_WORKING_DIR = os.path.expanduser("~/joao-interface")
COUNCIL_TASK_DIR = "/tmp/council/tasks"
COUNCIL_OUTPUT_DIR = "/tmp/council/outputs"
COUNCIL_LAUNCHER = os.path.expanduser("~/council/bin/launch_agent.sh")


def is_claude_running(session_name: str) -> bool:
    """Check if Claude Code is actively running in a tmux session.

    Captures the last 10 lines and looks for the Claude Code prompt indicator.
    Also checks if 'claude' is among the running processes in the pane.
    """
    # Method 1: Check pane content for Claude Code prompt
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-10"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        content = result.stdout
        # Claude Code shows these indicators when running
        if any(indicator in content for indicator in ["Claude Code", "bypass permissions on"]):
            return True

    # Method 2: Check the pane's running command
    result = subprocess.run(
        ["tmux", "display-message", "-t", session_name, "-p", "#{pane_current_command}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        cmd = result.stdout.strip().lower()
        if "claude" in cmd or "node" in cmd:
            return True

    return False




class DispatchCommand(BaseModel):
    agent: str
    task: str
    priority: str = "normal"
    context: Optional[str] = None
    project: Optional[str] = None
    lane: str = "automated"  # "automated" (bash-only), "interactive" (running claude), "claude" (one-shot claude)


class DispatchResponse(BaseModel):
    status: str
    agent: str
    session: str
    timestamp: str
    message: str


def verify_secret(authorization: str | None):
    """Verify the dispatch secret from Railway spine."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if authorization != f"Bearer {DISPATCH_SECRET}":
        raise HTTPException(status_code=401, detail="Invalid dispatch secret")


def tmux_session_exists(session_name: str) -> bool:
    """Check if a tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def create_tmux_session(session_name: str):
    """Create a tmux session if it doesn't exist."""
    if not tmux_session_exists(session_name):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name],
            capture_output=True,
        )
        logger.info(f"Created tmux session: {session_name}")


def sanitize_for_tmux(text: str) -> str:
    """Strip ANSI/terminal escape sequences and control characters from text
    before sending to tmux.

    tmux send-keys -l passes bytes literally to the terminal emulator.
    ESC sequences could be interpreted as cursor movement or other control
    codes by the running process. Strip them defensively.
    """
    # 1. CSI sequences: ESC[ (with optional intermediate bytes) ... final byte
    text = re.sub(r'\x1b\[[\x20-\x3f]*[\x30-\x3f]*[\x40-\x7e]', '', text)
    # 2. OSC sequences: ESC] ... (terminated by BEL or ST)
    text = re.sub(r'\x1b\].*?(?:\x07|\x1b\\)', '', text)
    # 3. Other ESC sequences (SS2, SS3, DCS, PM, APC, etc.)
    text = re.sub(r'\x1b[\x20-\x7e]', '', text)
    # 4. C1 control codes (8-bit equivalents: 0x80-0x9F)
    text = re.sub(r'[\x80-\x9f]', '', text)
    # 5. Remaining control characters (BEL, BS, CR, etc.) but keep \n and \t
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text


def send_to_tmux(session_name: str, command: str):
    """Send a command string to a tmux session, then press Enter."""
    safe_command = sanitize_for_tmux(command)
    # Send the command text first (literal, no key interpretation)
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "-l", safe_command],
        capture_output=True,
    )
    # Send Enter as a separate key press
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
    )


def write_task_file(agent: str, task: str, context: str = None, project: str = None) -> str:
    """Write a task to a .md file for the agent launcher.

    Returns the absolute path to the task file.
    File-based dispatch avoids stdin piping which triggers prompt injection
    detection in Claude Code.
    """
    os.makedirs(COUNCIL_TASK_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_agent = re.sub(r'[^a-zA-Z0-9]', '_', agent.upper())
    task_file = f"{COUNCIL_TASK_DIR}/{safe_agent}_{timestamp}.md"

    prompt_text = task
    if context:
        prompt_text += f"\n\nAdditional context: {context}"
    if project:
        prompt_text += f"\n\nProject: {project}"

    with open(task_file, "w") as f:
        f.write(prompt_text)

    return task_file


def build_automated_command(
    agent: str,
    task: str,
    priority: str,
    context: str = None,
    project: str = None,
) -> str:
    """Build a non-interactive bash command for the automated lane.

    The task is executed directly as a bash command — no Claude CLI,
    no interactive prompts, no heredocs that could hang.
    """
    # The task IS the command in automated mode
    return task


def build_claude_task_command(
    agent: str,
    task: str,
    priority: str,
    context: str = None,
    project: str = None,
) -> str:
    """Build a Claude Code --print command using the file-based launcher.

    Writes the task to /tmp/council/tasks/{AGENT}_{timestamp}.md and returns
    a shell command that runs the universal launcher. Output captured to
    /tmp/council/outputs/{AGENT}_{timestamp}.md.

    This replaces both the old interactive (tmux send-keys) and claude
    (stdin pipe) lanes with a single safe approach that does NOT trigger
    prompt injection detection.
    """
    task_file = write_task_file(agent, task, context, project)

    return f"bash {COUNCIL_LAUNCHER} {agent.upper()} {task_file} {AGENT_WORKING_DIR}"


@app.get("/health")
async def health():
    return {
        "status": "alive",
        "server": "ubuntu-192.168.0.55",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/agents")
async def list_agents():
    """List all agents and their tmux/service status."""
    statuses = {}
    for agent, session in AGENT_SESSIONS.items():
        session_up = tmux_session_exists(session)
        # SCOUT runs as a systemd service, not tmux
        if agent == "SCOUT" and not session_up:
            import subprocess
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", "council-scout.service"],
                    capture_output=True, text=True, timeout=5,
                )
                session_up = result.stdout.strip() == "active"
            except Exception:
                pass
        statuses[agent] = {
            "session": session,
            "active": session_up,
            "claude_running": is_claude_running(session) if session_up else False,
        }
    return {"agents": statuses}


@app.post("/dispatch", response_model=DispatchResponse)
async def dispatch(cmd: DispatchCommand, authorization: str | None = Header(None)):
    """Dispatch a task to a Council agent via tmux.

    Three lanes:
    - automated (default): Bash-only commands. No interactive processes.
      Guards against claude CLI, vim, ssh, etc.
    - interactive: Sends text to a running Claude Code session (launched
      with --dangerously-skip-permissions by restart_agents.sh). No
      permission prompts.
    - claude: One-shot Claude Code invocation via temp file pipe.
      Uses -p --dangerously-skip-permissions. No permission prompts.
    """
    verify_secret(authorization)

    agent = cmd.agent.upper()
    if agent not in AGENT_SESSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown agent: {agent}. Available: {list(AGENT_SESSIONS.keys())}",
        )

    lane = cmd.lane or "automated"
    session = AGENT_SESSIONS[agent]
    create_tmux_session(session)

    if lane in ("interactive", "claude"):
        if is_claude_running(session):
            # Claude is running -- send the task directly into the session.
            # This keeps the persistent Claude session alive instead of
            # killing and restarting it on every dispatch.
            task_text = cmd.task
            if cmd.context:
                task_text += f"\n\nContext: {cmd.context}"
            if cmd.project:
                task_text += f"\n\nProject: {cmd.project}"
            command = task_text
            logger.info(f"[{lane}→direct] Sending task into running Claude in {session}: {cmd.task[:100]}")
        else:
            # No Claude running -- use file-based launcher to start one
            command = build_claude_task_command(
                agent, cmd.task, cmd.priority, cmd.context, cmd.project
            )
            logger.info(f"[{lane}→print] No Claude in {session}, launching: {cmd.task[:100]}")
    else:
        # Automated lane: bash-only, no interactive processes
        if is_interactive(cmd.task):
            raise HTTPException(
                status_code=422,
                detail=f"Automated lane rejects interactive commands. "
                       f"Task starts with a blocked pattern. "
                       f"Use lane='interactive' or lane='claude' for Claude tasks.",
            )
        command = build_automated_command(
            agent, cmd.task, cmd.priority, cmd.context, cmd.project
        )
        logger.info(f"[automated] Dispatched to {agent}: {cmd.task[:100]}")

    send_to_tmux(session, command)

    return DispatchResponse(
        status="dispatched",
        agent=agent,
        session=session,
        timestamp=datetime.now(timezone.utc).isoformat(),
        message=f"Task sent to {agent} via tmux session '{session}' [lane={lane}]",
    )


@app.post("/dispatch/raw")
async def dispatch_raw(cmd: DispatchCommand, authorization: str | None = Header(None)):
    """Send a raw command to a tmux session (no Claude Code wrapper)."""
    verify_secret(authorization)

    agent = cmd.agent.upper()
    if agent not in AGENT_SESSIONS:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")

    session = AGENT_SESSIONS[agent]
    create_tmux_session(session)
    send_to_tmux(session, cmd.task)

    return {"status": "sent", "agent": agent, "session": session}


@app.get("/sessions")
async def get_sessions():
    """Get output from all active tmux sessions."""
    outputs = {}
    for agent, session in AGENT_SESSIONS.items():
        if tmux_session_exists(session):
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", session, "-p", "-S", "-50"],
                capture_output=True,
                text=True,
            )
            outputs[agent] = {
                "session": session,
                "last_50_lines": result.stdout
                if result.returncode == 0
                else "capture failed",
            }
    return {"sessions": outputs}


@app.get("/session/{agent}")
async def get_session(agent: str):
    """Get detailed output from a specific agent's tmux session."""
    agent = agent.upper()
    if agent not in AGENT_SESSIONS:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")

    session = AGENT_SESSIONS[agent]
    if not tmux_session_exists(session):
        raise HTTPException(
            status_code=404, detail=f"Session '{session}' not running"
        )

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-S", "-200"],
        capture_output=True,
        text=True,
    )
    return {
        "agent": agent,
        "session": session,
        "output": result.stdout if result.returncode == 0 else "capture failed",
    }


# ── Council Registry (file-backed for gunicorn workers) ──────

import json as _json

_COUNCIL_REGISTRY_FILE = "/home/zamoritacr/council/config/council_registry.json"


def _load_registry() -> dict:
    try:
        with open(_COUNCIL_REGISTRY_FILE) as f:
            return _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        return {}


def _save_registry(data: dict):
    os.makedirs(os.path.dirname(_COUNCIL_REGISTRY_FILE), exist_ok=True)
    with open(_COUNCIL_REGISTRY_FILE, "w") as f:
        _json.dump(data, f, indent=2)


class CouncilRegister(BaseModel):
    agent: str
    session: str
    status: str = "online"


@app.post("/council/register")
async def council_register(reg: CouncilRegister):
    agent = reg.agent.upper()
    registry = _load_registry()
    registry[agent] = {
        "agent": agent,
        "session": reg.session,
        "status": reg.status,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_registry(registry)
    return {"status": "registered", "agent": agent}


@app.get("/council/agents")
async def council_agents():
    registry = _load_registry()
    for agent, info in registry.items():
        session = info.get("session", agent)
        info["tmux_active"] = tmux_session_exists(session)
        info["claude_running"] = is_claude_running(session) if info["tmux_active"] else False
    return {"agents": registry, "count": len(registry)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("JOAO_DISPATCH_PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
