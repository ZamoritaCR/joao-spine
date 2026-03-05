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


def launch_claude_in_session(session_name: str):
    """Launch Claude Code with --dangerously-skip-permissions in a tmux session.

    Activates the venv, cd's to the working dir, and starts Claude Code.
    Waits briefly for Claude Code to initialize.
    """
    import time

    logger.warning(f"Claude Code NOT running in '{session_name}' -- auto-launching")

    # Activate venv
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "-l", VENV_ACT],
        capture_output=True,
    )
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
    )
    time.sleep(0.3)

    # cd to working directory
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "-l", f"cd {AGENT_WORKING_DIR}"],
        capture_output=True,
    )
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
    )
    time.sleep(0.3)

    # Launch Claude Code
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "-l",
         f"{CLAUDE_BIN_PATH} --dangerously-skip-permissions"],
        capture_output=True,
    )
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
    )

    # Wait for Claude Code to initialize
    time.sleep(5)
    logger.info(f"Claude Code auto-launched in '{session_name}'")


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
    """Build the Claude Code prompt for the interactive lane.

    Returns the prompt text directly, to be typed into the agent's
    tmux window where Claude Code is already running with
    --dangerously-skip-permissions (launched by restart_agents.sh).
    The running Claude Code instance receives this as a normal user prompt.
    """
    prompt_text = task
    if context:
        prompt_text += f"\n\nAdditional context: {context}"
    if project:
        prompt_text += f"\n\nProject: {project}"

    return prompt_text


def build_claude_oneshot(
    agent: str,
    task: str,
    priority: str,
    context: str = None,
    project: str = None,
) -> str:
    """Build a one-shot Claude Code command for the 'claude' lane.

    Launches claude -p --dangerously-skip-permissions with the task piped
    via a temp file. Used when no Claude Code session is already running
    in the agent's tmux pane.
    """
    prompt_text = task
    if context:
        prompt_text += f"\n\nAdditional context: {context}"
    if project:
        prompt_text += f"\n\nProject: {project}"

    # Write prompt to temp file, pipe to claude -p (non-interactive print mode)
    safe_agent = re.sub(r'[^a-zA-Z0-9]', '_', agent.lower())
    prompt_file = f"/tmp/council_prompt_{safe_agent}.txt"

    # Write the prompt file
    with open(prompt_file, "w") as f:
        f.write(prompt_text)

    return f"cd {AGENT_WORKING_DIR} && {CLAUDE_BIN_PATH} -p --dangerously-skip-permissions < {prompt_file}"


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
        session_up = tmux_session_exists(session)
        statuses[agent] = {
            "session": session,
            "tmux_active": session_up,
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

    if lane == "interactive":
        # Interactive lane: text sent to running Claude Code session
        # (already launched with --dangerously-skip-permissions)
        # SAFETY: verify Claude Code is actually running before sending prompts
        if not is_claude_running(session):
            logger.warning(
                f"[interactive] Claude Code not running in {session} for {agent}. "
                f"Auto-launching before dispatch."
            )
            launch_claude_in_session(session)
            # Verify it came up
            if not is_claude_running(session):
                logger.error(f"[interactive] Failed to auto-launch Claude Code for {agent}")
                raise HTTPException(
                    status_code=503,
                    detail=f"Claude Code is not running in {agent}'s session and "
                           f"auto-launch failed. Use lane='claude' for one-shot, "
                           f"or restart the agent manually.",
                )
        command = build_interactive_prompt(
            agent, cmd.task, cmd.priority, cmd.context, cmd.project
        )
        logger.info(f"[interactive] Dispatched to {agent}: {cmd.task[:100]}")
    elif lane == "claude":
        # Claude lane: one-shot claude -p --dangerously-skip-permissions
        command = build_claude_oneshot(
            agent, cmd.task, cmd.priority, cmd.context, cmd.project
        )
        logger.info(f"[claude] Dispatched to {agent}: {cmd.task[:100]}")
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("JOAO_DISPATCH_PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
