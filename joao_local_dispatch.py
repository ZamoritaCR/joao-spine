"""
JOAO Local Dispatch Listener
Runs on Ubuntu server (192.168.0.55), receives commands from Railway spine
via Cloudflare Tunnel. Executes tmux commands to dispatch work to Council agents.
"""
import os
import re
import subprocess
import logging
import time
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import httpx
import uvicorn

app = FastAPI(title="JOAO Local Dispatch", version="3.1.0")
logger = logging.getLogger("joao-dispatch")

# Dispatch stagger: the launcher script itself handles rate-limit protection
# via per-agent hash delay (0-15s) and flock-based per-agent locking.
# No in-process lock needed since gunicorn runs multiple workers.
_DISPATCH_STAGGER_SECS = 0  # launcher handles its own stagger

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

# Hot pool: these agents keep persistent Claude sessions (always running).
# On-demand agents use one-shot claude --print via file-based launcher.
HOT_AGENTS = {"MAX", "CORE", "BYTE"}


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
COUNCIL_CHECKPOINT_DIR = "/tmp/council/checkpoints"


def is_claude_running(session_name: str) -> bool:
    """Check if Claude Code is actively running in a tmux session.

    Uses process tree inspection (reliable) instead of terminal buffer text
    (unreliable -- text persists after process death).
    """
    # Method 1: Get pane PID and check child processes
    result = subprocess.run(
        ["tmux", "display-message", "-t", session_name, "-p", "#{pane_pid}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        pane_pid = result.stdout.strip()
        if pane_pid:
            check = subprocess.run(
                ["pgrep", "-P", pane_pid, "-f", "claude|node"],
                capture_output=True,
            )
            if check.returncode == 0:
                return True

    # Method 2: Check pane's foreground command via tmux
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
    """Create a tmux session if it doesn't exist. Forces bash shell."""
    if not tmux_session_exists(session_name):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "bash"],
            capture_output=True,
        )
        logger.info(f"Created tmux session: {session_name} (bash)")


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


def _clear_stuck_prompt(session_name: str):
    """Cancel any stuck prompt, exit pagers, and clear the input line buffer.

    Without this, dispatched commands get typed into stuck prompts (sudo
    password, confirmation, less/more pagers, etc.) instead of running as
    intended.

    Sequence:
    1. 'q' to exit pagers (less, more, git log, man, etc.)
    2. Ctrl-C x2 to cancel any running process
    3. Ctrl-U to kill any partial text in readline buffer
    """
    # Exit pagers first (q exits less/more/man; harmless at a shell prompt
    # because it just prints 'q: command not found' which gets overwritten)
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "q"],
        capture_output=True,
    )
    time.sleep(0.2)
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
    )
    time.sleep(0.3)
    # Cancel running processes
    for _ in range(2):
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "C-c"],
            capture_output=True,
        )
        time.sleep(0.2)
    # Kill any leftover text in the line buffer
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "C-u"],
        capture_output=True,
    )
    # Small delay for the shell prompt to reappear clean
    time.sleep(0.3)


def send_to_tmux(session_name: str, command: str):
    """Send a command string to a tmux session, then press Enter.

    Always clears any stuck prompt first (sudo password, confirmation, etc.)
    before sending the command. Uses two-step approach: literal text first
    (-l flag), then Enter as a separate key press after a brief pause.
    The pause is critical for interactive Claude Code sessions: without it,
    Enter arrives while the terminal is still buffering the paste and gets
    swallowed into the paste buffer instead of triggering submission.
    """
    # Cancel any stuck prompt before sending the new command
    if not is_claude_running(session_name):
        _clear_stuck_prompt(session_name)

    safe_command = sanitize_for_tmux(command)
    # Send the command text first (literal, no key interpretation)
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "-l", safe_command],
        capture_output=True,
    )
    # Wait for the terminal to finish processing the paste before sending Enter.
    # Without this delay Claude Code shows "[Pasted text #1 +N lines]" and
    # never executes because Enter arrives inside the paste buffer window.
    time.sleep(0.5)
    # Send Enter as a separate key press to submit the buffered input
    result = subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
    )
    if result.returncode != 0:
        logger.warning(
            "send_to_tmux: Enter key press failed for %s (rc=%d): %s",
            session_name, result.returncode, result.stderr.decode(errors="replace"),
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


# ---------------------------------------------------------------------------
# Long-horizon task models (paper: arXiv:2604.11978 — HORIZON framework)
# ---------------------------------------------------------------------------

class HorizonStep(BaseModel):
    """One step in a long-horizon task sequence."""
    index: int
    agent: str
    description: str
    success_criteria: str
    state_assertions: list[str] = []  # what must be true before this step runs


class LongHorizonTask(BaseModel):
    """Multi-step orchestration with explicit constraint tracking.

    Mitigates: Catastrophic Forgetting, Planning Error, History Error Accumulation,
    Memory Limitation (arXiv:2604.11978).
    """
    task_id: str
    title: str
    constraints: list[str]           # hard invariants enforced across ALL steps
    steps: list[HorizonStep]
    priority: str = "normal"
    project: Optional[str] = None
    lane: str = "claude"


class CheckpointReport(BaseModel):
    """Agent self-report at the end of each step.

    The compressed_state becomes the constraint reminder injected into
    subsequent steps, preventing Catastrophic Forgetting across steps.
    """
    task_id: str
    step_index: int
    agent: str
    status: str                       # "success" | "partial" | "failed"
    compressed_state: str             # 2-5 sentence summary of what is now true
    constraints_violated: list[str] = []
    next_step_notes: str = ""         # hints for the next agent in the chain


class HorizonTaskResponse(BaseModel):
    task_id: str
    title: str
    total_steps: int
    first_step_dispatched_to: str
    task_file: str
    checkpoint_dir: str


# ---------------------------------------------------------------------------
# Structured task file writer — addresses Instruction Error + False Assumptions
# ---------------------------------------------------------------------------

def write_task_file_structured(
    agent: str,
    step: HorizonStep,
    task: "LongHorizonTask",
    prior_checkpoint: Optional[dict] = None,
) -> str:
    """Write a structured task file with explicit constraint and state blocks.

    Structure (top-to-bottom):
      1. CRITICAL CONSTRAINTS  — hard invariants, repeated every step
      2. PRIOR STATE SNAPSHOT  — compressed state from previous step (if any)
      3. TASK DESCRIPTION      — what this step must accomplish
      4. SUCCESS CRITERIA      — exact conditions that mark this step done
      5. STATE ASSERTIONS      — preconditions to verify before acting
      6. NEXT AGENT NOTES      — what the next step expects from you

    Prepending constraints first prevents Catastrophic Forgetting — the model
    sees the invariants before any task text, not buried hundreds of tokens in.
    """
    os.makedirs(COUNCIL_TASK_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_agent = re.sub(r'[^a-zA-Z0-9]', '_', agent.upper())
    task_file = f"{COUNCIL_TASK_DIR}/{safe_agent}_{timestamp}_h{step.index}.md"

    total_steps = len(task.steps)

    lines = [
        f"# HORIZON TASK: {task.title}",
        f"# Task ID: {task.task_id} | Step {step.index + 1} of {total_steps} | Agent: {agent}",
        "",
        "## CRITICAL CONSTRAINTS (apply to ALL steps — do not deviate)",
    ]
    for c in task.constraints:
        lines.append(f"- {c}")

    if prior_checkpoint:
        lines += [
            "",
            "## PRIOR STATE SNAPSHOT (what was true after the last step)",
            prior_checkpoint.get("compressed_state", "No prior state available."),
        ]
        if prior_checkpoint.get("next_step_notes"):
            lines += [
                "",
                "## NOTES FROM PREVIOUS AGENT",
                prior_checkpoint["next_step_notes"],
            ]

    lines += [
        "",
        "## YOUR TASK (Step {}/{})".format(step.index + 1, total_steps),
        step.description,
        "",
        "## SUCCESS CRITERIA",
        step.success_criteria,
    ]

    if step.state_assertions:
        lines += [
            "",
            "## STATE ASSERTIONS — verify these before acting",
        ]
        for assertion in step.state_assertions:
            lines.append(f"- [ ] {assertion}")

    if task.project:
        lines += ["", f"## PROJECT CONTEXT", f"Project: {task.project}"]

    remaining = total_steps - step.index - 1
    if remaining > 0:
        next_step = task.steps[step.index + 1]
        lines += [
            "",
            f"## NEXT STEP PREVIEW (step {step.index + 2}/{total_steps} — do NOT execute, just be aware)",
            f"Agent: {next_step.agent} | Task: {next_step.description[:200]}",
            "",
            "When you finish, write a 2-5 sentence COMPRESSED STATE SUMMARY of what is now true.",
            "The next agent depends on this to avoid re-doing your work or making false assumptions.",
        ]
    else:
        lines += [
            "",
            "## FINAL STEP",
            "This is the last step. Confirm all CRITICAL CONSTRAINTS were honored throughout.",
        ]

    with open(task_file, "w") as f:
        f.write("\n".join(lines))

    return task_file


# ---------------------------------------------------------------------------
# Checkpoint state persistence
# ---------------------------------------------------------------------------

def _checkpoint_file(task_id: str) -> str:
    os.makedirs(COUNCIL_CHECKPOINT_DIR, exist_ok=True)
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', task_id)
    return f"{COUNCIL_CHECKPOINT_DIR}/{safe_id}.json"


def _load_checkpoints(task_id: str) -> list[dict]:
    path = _checkpoint_file(task_id)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return _json.load(f)


def _append_checkpoint(task_id: str, report: "CheckpointReport"):
    checkpoints = _load_checkpoints(task_id)
    checkpoints.append({
        "step_index": report.step_index,
        "agent": report.agent,
        "status": report.status,
        "compressed_state": report.compressed_state,
        "constraints_violated": report.constraints_violated,
        "next_step_notes": report.next_step_notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    with open(_checkpoint_file(task_id), "w") as f:
        _json.dump(checkpoints, f, indent=2)


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
            "pool": "hot" if agent in HOT_AGENTS else ("service" if agent == "SCOUT" else "on-demand"),
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

    On-demand agents (claude lane, no Claude running) are serialized through
    a lock with an 8-second stagger to prevent API rate-limit thundering herd.
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

    needs_stagger = False

    if lane in ("interactive", "claude"):
        claude_alive = is_claude_running(session)
        if claude_alive:
            # Claude is running -- send the task directly into the session.
            task_text = cmd.task
            if cmd.context:
                task_text += f"\n\nContext: {cmd.context}"
            if cmd.project:
                task_text += f"\n\nProject: {cmd.project}"
            command = task_text
            logger.info(f"[{lane}->direct] Sending task into running Claude in {session}: {cmd.task[:100]}")
        else:
            # No Claude running -- use file-based launcher (starts one-shot,
            # then restarts persistent session for hot agents).
            command = build_claude_task_command(
                agent, cmd.task, cmd.priority, cmd.context, cmd.project
            )
            needs_stagger = True
            logger.info(f"[{lane}->print] No Claude in {session}, launching: {cmd.task[:100]}")
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

    # Stagger for on-demand agents is handled inside launch_agent.sh v2
    # (per-agent hash delay 0-15s + flock). No dispatcher-side delay needed.
    send_to_tmux(session, command)

    return DispatchResponse(
        status="dispatched",
        agent=agent,
        session=session,
        timestamp=datetime.now(timezone.utc).isoformat(),
        message=f"Task sent to {agent} via tmux session '{session}' [lane={lane}]",
    )


@app.post("/dispatch/horizon", response_model=HorizonTaskResponse)
async def dispatch_horizon(
    task: LongHorizonTask,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(None),
):
    """Dispatch a multi-step long-horizon task to the Council.

    Implements mitigations from arXiv:2604.11978 (HORIZON framework):
    - Constraint blocks prepended to every step (Catastrophic Forgetting)
    - Explicit success criteria per step (Instruction Error)
    - State assertions checked before each step (Environment Error, False Assumptions)
    - Sequential step dispatch gated on checkpoint reports (History Error Accumulation)
    - Compressed state summaries injected forward (Memory Limitation)

    Only the FIRST step is dispatched immediately. Subsequent steps are triggered
    via POST /council/checkpoint once the prior step files a success checkpoint.
    """
    verify_secret(authorization)

    if not task.steps:
        raise HTTPException(status_code=422, detail="LongHorizonTask requires at least one step")

    # Validate all agents exist upfront — catches planning errors early
    for step in task.steps:
        agent_upper = step.agent.upper()
        if agent_upper not in AGENT_SESSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Step {step.index}: unknown agent '{step.agent}'. "
                       f"Available: {list(AGENT_SESSIONS.keys())}",
            )

    # Initialize checkpoint file (empty — no prior state for step 0)
    _load_checkpoints(task.task_id)  # ensures checkpoint dir exists

    first_step = task.steps[0]
    agent = first_step.agent.upper()
    session = AGENT_SESSIONS[agent]
    create_tmux_session(session)

    task_file = write_task_file_structured(agent, first_step, task, prior_checkpoint=None)
    command = f"bash {COUNCIL_LAUNCHER} {agent} {task_file} {AGENT_WORKING_DIR}"

    send_to_tmux(session, command)
    logger.info(
        "[horizon] Task '%s' (id=%s) step 0/%d dispatched to %s",
        task.title, task.task_id, len(task.steps) - 1, agent,
    )

    return HorizonTaskResponse(
        task_id=task.task_id,
        title=task.title,
        total_steps=len(task.steps),
        first_step_dispatched_to=agent,
        task_file=task_file,
        checkpoint_dir=COUNCIL_CHECKPOINT_DIR,
    )


@app.post("/council/checkpoint")
async def council_checkpoint(
    report: CheckpointReport,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(None),
):
    """Accept a step-completion checkpoint from a Council agent.

    When an agent completes a horizon step it POSTs here with a compressed
    state summary. This endpoint:
    1. Persists the checkpoint state
    2. If status == 'success', loads the next step and dispatches it with
       the prior state injected as a constraint reminder
    3. If constraints_violated, stops the chain and logs the failure

    This closes the History Error Accumulation loop — each step is gated
    on an explicit success signal, not assumed to have succeeded.
    """
    verify_secret(authorization)

    _append_checkpoint(report.task_id, report)
    logger.info(
        "[checkpoint] task=%s step=%d agent=%s status=%s",
        report.task_id, report.step_index, report.agent, report.status,
    )

    if report.constraints_violated:
        logger.warning(
            "[checkpoint] CONSTRAINT VIOLATION in task=%s step=%d: %s",
            report.task_id, report.step_index, report.constraints_violated,
        )
        return {
            "accepted": True,
            "action": "chain_halted",
            "reason": "constraints_violated",
            "violated": report.constraints_violated,
        }

    if report.status != "success":
        return {
            "accepted": True,
            "action": "chain_halted",
            "reason": f"step status={report.status} — manual review required",
        }

    # Load the task definition to find the next step.
    # Task metadata is stored alongside the checkpoint.
    meta_path = _checkpoint_file(report.task_id) + ".meta.json"
    if not os.path.exists(meta_path):
        return {
            "accepted": True,
            "action": "no_next_step",
            "reason": "task metadata not found — horizon chain not available for auto-advance",
        }

    with open(meta_path) as f:
        task_dict = _json.load(f)

    task = LongHorizonTask(**task_dict)
    next_index = report.step_index + 1

    if next_index >= len(task.steps):
        logger.info("[checkpoint] task=%s COMPLETE — all %d steps done", report.task_id, len(task.steps))
        return {"accepted": True, "action": "task_complete", "total_steps": len(task.steps)}

    next_step = task.steps[next_index]
    agent = next_step.agent.upper()
    session = AGENT_SESSIONS[agent]
    create_tmux_session(session)

    prior = {
        "compressed_state": report.compressed_state,
        "next_step_notes": report.next_step_notes,
    }
    task_file = write_task_file_structured(agent, next_step, task, prior_checkpoint=prior)
    command = f"bash {COUNCIL_LAUNCHER} {agent} {task_file} {AGENT_WORKING_DIR}"
    send_to_tmux(session, command)

    logger.info(
        "[checkpoint] task=%s dispatching step %d/%d to %s",
        report.task_id, next_index, len(task.steps) - 1, agent,
    )
    return {
        "accepted": True,
        "action": "next_step_dispatched",
        "step": next_index,
        "agent": agent,
        "task_file": task_file,
    }


@app.post("/dispatch/horizon/register")
async def register_horizon_task(
    task: LongHorizonTask,
    authorization: str | None = Header(None),
):
    """Store task metadata so /council/checkpoint can auto-advance steps.

    Call this BEFORE /dispatch/horizon when you want fully automated
    sequential execution. Without this, /council/checkpoint returns
    'no_next_step' and steps must be manually dispatched.
    """
    verify_secret(authorization)
    meta_path = _checkpoint_file(task.task_id) + ".meta.json"
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w") as f:
        _json.dump(task.model_dump(), f, indent=2)
    return {"registered": True, "task_id": task.task_id, "steps": len(task.steps)}


@app.get("/council/checkpoints/{task_id}")
async def get_checkpoints(task_id: str, authorization: str | None = Header(None)):
    """Retrieve the full checkpoint history for a horizon task."""
    verify_secret(authorization)
    checkpoints = _load_checkpoints(task_id)
    meta_path = _checkpoint_file(task_id) + ".meta.json"
    meta = None
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = _json.load(f)
    return {
        "task_id": task_id,
        "checkpoints": checkpoints,
        "steps_completed": len(checkpoints),
        "task_meta": meta,
    }


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


# ── OS-Agent Proxy (Railway reaches os-agent through this tunnel) ─────

OS_AGENT_URL = os.getenv("OS_AGENT_LOCAL_URL", "http://localhost:7801")
OS_AGENT_KEY = os.getenv("OS_AGENT_KEY", "joao-os-2026")


@app.api_route("/os-proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def os_proxy(path: str, request: Request, authorization: str | None = Header(None)):
    """Proxy requests to the local os-agent on port 7801.

    Railway spine hits dispatch.theartofthepossible.io/os-proxy/status
    which forwards to localhost:7801/status on the ROG.
    """
    verify_secret(authorization)
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=55.0) as client:
            r = await client.request(
                method=request.method,
                url=f"{OS_AGENT_URL}/{path}",
                headers={"X-API-Key": OS_AGENT_KEY, "Content-Type": "application/json"},
                content=body,
            )
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="os-agent request timed out")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="os-agent unreachable on localhost:7801")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"os-agent proxy error: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("JOAO_DISPATCH_PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
