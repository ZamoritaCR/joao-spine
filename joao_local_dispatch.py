"""
JOAO Local Dispatch Listener
Runs on Ubuntu server (192.168.0.55), receives commands from Railway spine
via Cloudflare Tunnel. Executes tmux commands to dispatch work to Council agents.
"""
import os
import subprocess
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import uvicorn

app = FastAPI(title="JOAO Local Dispatch", version="2.0.0")
logger = logging.getLogger("joao-dispatch")

# Security: shared secret between Railway and local listener
DISPATCH_SECRET = os.getenv("JOAO_DISPATCH_SECRET", "CHANGE_ME_IN_PRODUCTION")

# Agent -> tmux session mapping
AGENT_SESSIONS = {
    "BYTE": "byte",
    "ARIA": "aria",
    "CJ": "cj",
    "SOFIA": "sofia",
    "DEX": "dex",
    "GEMMA": "gemma",
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


class DispatchCommand(BaseModel):
    agent: str
    task: str
    priority: str = "normal"
    context: Optional[str] = None
    project: Optional[str] = None
    lane: str = "automated"  # "automated" (bash-only) or "interactive" (claude CLI)


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


def send_to_tmux(session_name: str, command: str):
    """Send a command string to a tmux session."""
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, command, "Enter"],
        capture_output=True,
    )


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


def build_interactive_prompt(
    agent: str,
    task: str,
    priority: str,
    context: str = None,
    project: str = None,
) -> str:
    """Build the Claude Code prompt for the interactive lane (human-supervised only)."""
    context_line = f"\nCONTEXT: {context}" if context else ""
    prompt = f"""claude --dangerously-skip-permissions << 'JOAO_DISPATCH'
PROJECT: {project or 'JOAO System'}
DISPATCHED BY: JOAO (via Railway Spine)
PRIORITY: {priority.upper()}
TIMESTAMP: {datetime.now(timezone.utc).isoformat()}
AGENT: {agent}

TASK:
{task}
{context_line}

RULES:
- Git commit after each phase with descriptive message
- If blocked, make reasonable decision and document it
- Prioritize shipping over perfection
- Test each feature before moving to next
- NO refactoring of existing working code
- Report completion to Supabase dispatch_log table when done
JOAO_DISPATCH"""
    return prompt


@app.get("/health")
async def health():
    return {
        "status": "alive",
        "server": "ubuntu-192.168.0.55",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/agents")
async def list_agents():
    """List all agents and their tmux session status."""
    statuses = {}
    for agent, session in AGENT_SESSIONS.items():
        statuses[agent] = {
            "session": session,
            "active": tmux_session_exists(session),
        }
    return {"agents": statuses}


@app.post("/dispatch", response_model=DispatchResponse)
async def dispatch(cmd: DispatchCommand, authorization: str | None = Header(None)):
    """Dispatch a task to a Council agent via tmux.

    Two lanes:
    - automated (default): Bash-only commands. No interactive processes.
      Guards against claude CLI, vim, ssh, etc.
    - interactive: Claude CLI allowed. For human-supervised sessions only.
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

    if lane == "interactive":
        # Interactive lane: Claude CLI wrapper, human-supervised
        command = build_interactive_prompt(
            agent, cmd.task, cmd.priority, cmd.context, cmd.project
        )
        logger.info(f"[interactive] Dispatched to {agent}: {cmd.task[:100]}")
    else:
        # Automated lane: bash-only, no interactive processes
        if is_interactive(cmd.task):
            raise HTTPException(
                status_code=422,
                detail=f"Automated lane rejects interactive commands. "
                       f"Task starts with a blocked pattern. "
                       f"Use lane='interactive' for Claude CLI tasks.",
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=7777)
