#!/usr/bin/env python3
"""
TAOP Agent Workforce — CLI Interface
Usage:
    python -m interfaces.cli ask aria "Build me a landing page for dopamine.watch"
    python -m interfaces.cli ask auto "Research the streaming app market in LATAM"
    python -m interfaces.cli team "Review our pricing strategy" aria gemma cj
    python -m interfaces.cli status
    python -m interfaces.cli queue
    python -m interfaces.cli task 42
    python -m interfaces.cli agents
"""
import sys
import os
import textwrap

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import dispatch, dispatch_to_team, quick_ask
from core.agents import list_agents, get_agent, AGENTS
from core.tasks import get_stats, get_queue, get_task, get_recent

# ── Colors ──────────────────────────────────────────────────
R = "\033[0m"      # Reset
B = "\033[1m"      # Bold
DIM = "\033[2m"    # Dim
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"

AGENT_COLORS = {
    "aria": CYAN, "max": MAGENTA, "gemma": BLUE,
    "cj": CYAN, "sofia": MAGENTA, "dex": YELLOW,
    "luna": BLUE, "rafael": GREEN,
}

STATUS_COLORS = {
    "done": GREEN, "working": YELLOW, "queued": DIM, "failed": RED,
}


def print_header():
    print(f"""
{RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{R}
{B}  TAOP AGENT WORKFORCE — COMMAND LINE{R}
{DIM}  theartofthepossible.io // AI Army{R}
{RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{R}
""")


def cmd_agents():
    """List all available agents."""
    print_header()
    print(f"{B}  AGENT ROSTER{R}\n")
    for a in list_agents():
        c = AGENT_COLORS.get(a["name"].lower(), "")
        print(f"  {c}{a['emoji']} {a['name']:10}{R} {DIM}[{a['badge']}]{R}  {a['title']:30}  {DIM}engine:{R} {a['engine']}")
    print(f"\n  {DIM}Use: ask <agent> \"task\" or ask auto \"task\"{R}\n")


def cmd_ask(agent_name: str, task: str, priority: str = "P1"):
    """Send a task to an agent."""
    print_header()
    agent = get_agent(agent_name) if agent_name != "auto" else None
    
    if agent_name == "auto":
        from core.agents import find_best_agent
        routed = find_best_agent(task)
        agent = get_agent(routed)
        print(f"  {YELLOW}⚡ Auto-routed to: {agent['emoji']} {agent['name']} ({agent['title']}){R}\n")
    else:
        print(f"  {AGENT_COLORS.get(agent_name, '')}{agent['emoji']} Dispatching to {agent['name']}...{R}\n")
    
    result = dispatch(agent_name, task, priority=priority)
    
    if result["status"] == "done":
        print(f"  {GREEN}✓ TASK #{result['task_id']} COMPLETE{R} {DIM}({result['tokens_used']} tokens){R}\n")
        print(f"{DIM}{'─' * 60}{R}")
        # Wrap output for terminal readability
        for line in result["output"].split("\n"):
            print(f"  {line}")
        print(f"{DIM}{'─' * 60}{R}\n")
    else:
        print(f"  {RED}✗ TASK FAILED: {result.get('error', 'Unknown')}{R}\n")


def cmd_team(task: str, agents: list = None):
    """Send task to multiple agents."""
    print_header()
    agent_list = agents or ["aria", "max", "gemma"]
    print(f"  {B}📡 Broadcasting to: {', '.join(a.upper() for a in agent_list)}{R}\n")
    
    results = dispatch_to_team(task, agents=agent_list)
    
    for r in results:
        c = AGENT_COLORS.get(r.get("agent_key", ""), "")
        status_c = STATUS_COLORS.get(r["status"], "")
        print(f"  {c}{r['agent']:10}{R} {status_c}[{r['status'].upper()}]{R} {DIM}Task #{r['task_id']}{R}")
        if r["status"] == "done":
            # Show first 3 lines of output
            lines = r["output"].strip().split("\n")[:3]
            for line in lines:
                print(f"    {DIM}{line[:100]}{R}")
            if len(r["output"].strip().split("\n")) > 3:
                print(f"    {DIM}... (use: task {r['task_id']} to see full output){R}")
        elif r["status"] == "failed":
            print(f"    {RED}{r.get('error', '')[:100]}{R}")
        print()


def cmd_status():
    """Show task statistics."""
    print_header()
    stats = get_stats()
    print(f"  {B}TASK STATS{R}\n")
    print(f"  Total tasks:  {B}{stats['total_tasks']}{R}")
    print(f"  Total tokens: {B}{stats['total_tokens']:,}{R}\n")
    
    for status, count in stats["by_status"].items():
        c = STATUS_COLORS.get(status, "")
        print(f"  {c}{status.upper():10}{R} {count}")
    
    if stats["by_agent"]:
        print(f"\n  {B}BY AGENT{R}")
        for agent, data in stats["by_agent"].items():
            c = AGENT_COLORS.get(agent, "")
            print(f"  {c}{agent.upper():10}{R} {data['total']} total, {data['done']} done")
    print()


def cmd_queue(status: str = None):
    """Show task queue."""
    print_header()
    tasks = get_queue(status=status, limit=20) if status else get_recent(20)
    
    if not tasks:
        print(f"  {DIM}No tasks yet. Use: ask <agent> \"task\"{R}\n")
        return
    
    print(f"  {B}{'ID':5} {'AGENT':10} {'STATUS':10} {'PRIORITY':6} TITLE{R}")
    print(f"  {DIM}{'─' * 70}{R}")
    for t in tasks:
        c = AGENT_COLORS.get(t["agent"], "")
        sc = STATUS_COLORS.get(t["status"], "")
        print(f"  {t['id']:<5} {c}{t['agent'].upper():10}{R} {sc}{t['status']:10}{R} {t['priority']:6} {t['title'][:40]}")
    print()


def cmd_task(task_id: int):
    """Show full task details."""
    print_header()
    t = get_task(task_id)
    if not t:
        print(f"  {RED}Task #{task_id} not found.{R}\n")
        return
    
    c = AGENT_COLORS.get(t["agent"], "")
    sc = STATUS_COLORS.get(t["status"], "")
    
    print(f"  {B}TASK #{t['id']}{R} — {sc}{t['status'].upper()}{R}")
    print(f"  Agent:    {c}{t['agent'].upper()}{R}")
    print(f"  Priority: {t['priority']}")
    print(f"  Title:    {t['title']}")
    print(f"  Created:  {t['created_at']}")
    if t["completed_at"]:
        print(f"  Done:     {t['completed_at']}")
    if t["tokens_used"]:
        print(f"  Tokens:   {t['tokens_used']:,}")
    
    print(f"\n  {B}DESCRIPTION:{R}")
    print(f"  {t['description']}\n")
    
    if t["output"]:
        print(f"  {B}OUTPUT:{R}")
        print(f"  {DIM}{'─' * 60}{R}")
        for line in t["output"].split("\n"):
            print(f"  {line}")
        print(f"  {DIM}{'─' * 60}{R}")
    
    if t["error"]:
        print(f"\n  {RED}ERROR: {t['error']}{R}")
    print()


def print_usage():
    print_header()
    print(f"""  {B}COMMANDS:{R}

  {CYAN}agents{R}                              List all agents
  {CYAN}ask{R} <agent|auto> "task" [P0-P3]     Send task to agent
  {CYAN}team{R} "task" [agent1 agent2 ...]      Send to multiple agents
  {CYAN}status{R}                               Task statistics
  {CYAN}queue{R} [status]                        View task queue
  {CYAN}task{R} <id>                             View full task output

  {B}EXAMPLES:{R}

  {DIM}ask aria "Build a React dashboard for revenue tracking"
  ask auto "Research LATAM streaming market size"
  ask max "Write a FastAPI endpoint for user auth" P0
  ask dex "Draft a welcome email for new clients in Spanish"
  ask luna "Write 3 LinkedIn posts about AI workforce"
  team "Review dopamine.watch pricing strategy" aria gemma cj{R}
""")


# ── Main ────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    
    if not args or args[0] in ("help", "-h", "--help"):
        print_usage()
        return
    
    cmd = args[0].lower()
    
    if cmd == "agents":
        cmd_agents()
    
    elif cmd == "ask" and len(args) >= 3:
        agent = args[1].lower()
        task = args[2]
        priority = args[3] if len(args) > 3 else "P1"
        cmd_ask(agent, task, priority)
    
    elif cmd == "team" and len(args) >= 2:
        task = args[1]
        agents = args[2:] if len(args) > 2 else None
        cmd_team(task, agents)
    
    elif cmd == "status":
        cmd_status()
    
    elif cmd == "queue":
        status = args[1] if len(args) > 1 else None
        cmd_queue(status)
    
    elif cmd == "task" and len(args) >= 2:
        cmd_task(int(args[1]))
    
    else:
        print_usage()


if __name__ == "__main__":
    main()
