"""
Context Pack Builder -- assembles structured context before capability execution.

Per JOAO OS spec Section 5:
- operating_rules: from MEMORY.md + CLAUDE.md
- session_history: last 20 provenance entries filtered by relevance
- project_context: from target repo's CLAUDE.md + git status
- landmines: hardcoded known dangers + CLAUDE.md active bugs
- relevant_files: from capability contract + git diff
- definition_of_done: from capability contract or user-supplied
"""

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from exocortex.ledgers import get_intents

logger = logging.getLogger(__name__)

_HOME = Path.home()
_MEMORY_FILE = _HOME / ".claude" / "projects" / "-home-zamoritacr" / "memory" / "MEMORY.md"
_SPINE_DIR = _HOME / "joao-spine"
_CLAUDE_MD = _SPINE_DIR / "CLAUDE.md"

# Hardcoded landmines -- known dangers that never change
_HARDCODED_LANDMINES = [
    "SUPABASE_SERVICE_ROLE_KEY: use from env only, never log or commit",
    "WU internals: never send to external APIs (OpenAI, Anthropic, Google)",
    ".twbx data_file_path: must point to CSV, not archive (see BUG 5 fix)",
    "CORS: only allow known origins, not wildcard",
    "File uploads: sanitize filenames, enforce size limits",
]


def build_context_pack(
    project: str = "auto",
    task: str = "",
    capability: str = "",
    definition_of_done: str = "",
) -> dict:
    """Build a context pack per JOAO OS spec Section 5.

    Args:
        project: project name or "auto" to infer from task
        task: specific task description
        capability: capability being executed
        definition_of_done: user-supplied or from capability contract
    """
    pack = {
        "pack_id": f"ctx-{__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "assembled_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "sections": {},
    }

    # 1. Operating rules
    pack["sections"]["operating_rules"] = _build_operating_rules()

    # 2. Session history
    pack["sections"]["session_history"] = _build_session_history(project, capability)

    # 3. Project context
    pack["sections"]["project_context"] = _build_project_context(project)

    # 4. Landmines
    pack["sections"]["landmines"] = _build_landmines()

    # 5. Relevant files (minimal -- capability-driven)
    pack["sections"]["relevant_files"] = []

    # 6. Definition of done
    pack["sections"]["definition_of_done"] = definition_of_done or f"Capability '{capability}' completes without errors"

    # Hash the pack
    pack_json = json.dumps(pack, sort_keys=True, default=str)
    pack["hash"] = "sha256:" + hashlib.sha256(pack_json.encode()).hexdigest()

    return pack


def _build_operating_rules() -> dict:
    rules = {
        "adhd_header": "ADHD is superpower. Never slow down. Never guilt. Never coach.",
        "no_emojis": True,
        "source_files": [],
    }

    # Read MEMORY.md
    if _MEMORY_FILE.exists():
        try:
            content = _MEMORY_FILE.read_text(encoding="utf-8")[:2000]
            rules["memory_excerpt"] = content
            rules["source_files"].append(str(_MEMORY_FILE))
        except Exception:
            pass

    # Read CLAUDE.md
    if _CLAUDE_MD.exists():
        try:
            content = _CLAUDE_MD.read_text(encoding="utf-8")[:2000]
            rules["claude_md_excerpt"] = content
            rules["source_files"].append(str(_CLAUDE_MD))
        except Exception:
            pass

    return rules


def _build_session_history(project: str, capability: str) -> list[dict]:
    """Get last 20 provenance entries filtered by relevance."""
    try:
        intents = get_intents(last_n=40)
        # Filter by project or capability
        relevant = []
        for i in intents:
            if (project and project != "auto" and i.get("project") == project) or \
               (capability and capability in str(i.get("capability_chain", []))):
                relevant.append({
                    "timestamp": i.get("ts"),
                    "intent": i.get("parsed_intent"),
                    "autonomy": i.get("autonomy_level"),
                    "outcome": "recorded",
                    "run_id": i.get("intent_id"),
                })
        # If no relevant matches, return all recent
        if not relevant:
            relevant = [
                {
                    "timestamp": i.get("ts"),
                    "intent": i.get("parsed_intent"),
                    "autonomy": i.get("autonomy_level"),
                    "run_id": i.get("intent_id"),
                }
                for i in intents[-20:]
            ]
        return relevant[-20:]
    except Exception:
        return []


def _build_project_context(project: str) -> dict:
    """Build project context from repo state."""
    if project == "auto" or not project:
        return {"name": "auto", "note": "No specific project context requested"}

    # Try to find the repo
    from capability.git_adapter import _resolve_repo, _run_git
    path = _resolve_repo(project)
    if not path:
        return {"name": project, "note": f"Repo not found: {project}"}

    context = {"name": project, "path": path}

    # Branch + last commit
    out = _run_git(path, ["branch", "--show-current"])
    context["branch"] = out["stdout"] if out["success"] else "unknown"

    out = _run_git(path, ["log", "--oneline", "-1"])
    context["last_commit"] = out["stdout"] if out["success"] else "unknown"

    # Check for project-level CLAUDE.md
    claude_md = Path(path) / "CLAUDE.md"
    if claude_md.exists():
        try:
            content = claude_md.read_text(encoding="utf-8")[:1000]
            context["conventions"] = content
        except Exception:
            pass

    return context


def _build_landmines() -> list[str]:
    """Collect known dangers from hardcoded list + CLAUDE.md Active Bugs."""
    mines = list(_HARDCODED_LANDMINES)

    # Parse Active Bugs from CLAUDE.md if present
    if _CLAUDE_MD.exists():
        try:
            content = _CLAUDE_MD.read_text(encoding="utf-8")
            if "Active Bugs" in content or "ACTIVE BUGS" in content:
                in_bugs = False
                for line in content.split("\n"):
                    if "active bug" in line.lower() or "ACTIVE BUG" in line:
                        in_bugs = True
                        continue
                    if in_bugs:
                        if line.startswith("#") or line.startswith("---"):
                            break
                        stripped = line.strip("- \t")
                        if stripped:
                            mines.append(stripped)
        except Exception:
            pass

    return mines
