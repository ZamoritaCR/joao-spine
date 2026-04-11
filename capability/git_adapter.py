"""
Git Adapter -- tool sovereignty over local git repos.

Enforces autonomy levels:
- L0+: reads (status, diff, log, show, blame, ls-remote, grep)
- L3+WRITE_LOCK: writes (checkout -b, add, commit, branch)
- L4+SHIP_LOCK: ship (push, merge, tag)
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Known repos -- discovered at import time, updated on scan
KNOWN_REPOS: dict[str, str] = {}

# Candidate repo locations to discover
_REPO_CANDIDATES = [
    "/home/zamoritacr/joao-spine",
    "/home/zamoritacr/joao-interface",
    "/home/zamoritacr/joao-mcp",
    "/home/zamoritacr/joao-voice",
    "/home/zamoritacr/joao_autonomy",
    "/home/zamoritacr/taop-repos/joao-spine",
    "/home/zamoritacr/taop-repos/dr-data",
    "/home/zamoritacr/taop-repos/drdata-v2",
    "/home/zamoritacr/taop-repos/monster-mcp",
    "/home/zamoritacr/taop-repos/website-update",
    "/home/zamoritacr/projects/joao-spine",
    "/home/zamoritacr/projects/joao-computer-use",
    "/home/zamoritacr/projects/joao_flutter",
]


def _run_git(repo_path: str, args: list[str], timeout: int = 30) -> dict:
    """Run a git command in a repo. Returns {success, stdout, stderr}."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Command timed out"}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


def discover_repos() -> dict[str, str]:
    """Discover all git repos. Returns {name: path}."""
    repos = {}
    for candidate in _REPO_CANDIDATES:
        p = Path(candidate)
        if p.is_dir() and (p / ".git").exists():
            name = p.name
            # Disambiguate duplicates by parent dir
            if name in repos:
                name = f"{p.parent.name}/{name}"
            repos[name] = str(p)
    KNOWN_REPOS.update(repos)
    return repos


def _resolve_repo(repo_name: str) -> Optional[str]:
    """Resolve repo name to path."""
    if not KNOWN_REPOS:
        discover_repos()
    # Exact match
    if repo_name in KNOWN_REPOS:
        return KNOWN_REPOS[repo_name]
    # Case-insensitive match
    for name, path in KNOWN_REPOS.items():
        if name.lower() == repo_name.lower():
            return path
    # Path match
    if repo_name == "all":
        return None  # sentinel
    return None


def scan(repo: str = "all", since: str = "7d", query: str = "") -> dict:
    """Scan repos: status, branch, recent commits, uncommitted changes.

    Args:
        repo: repo name or "all"
        since: time range (e.g. "1d", "7d", "30d")
        query: optional grep query across repos
    """
    if not KNOWN_REPOS:
        discover_repos()

    if repo == "all":
        repos_to_scan = KNOWN_REPOS
    else:
        path = _resolve_repo(repo)
        if not path:
            return {"error": f"Unknown repo: {repo}", "known_repos": list(KNOWN_REPOS.keys())}
        repos_to_scan = {repo: path}

    results = {}
    for name, path in repos_to_scan.items():
        r = {"path": path}

        # Branch
        out = _run_git(path, ["branch", "--show-current"])
        r["branch"] = out["stdout"] if out["success"] else "detached"

        # HEAD
        out = _run_git(path, ["rev-parse", "--short", "HEAD"])
        r["head_sha"] = out["stdout"] if out["success"] else "unknown"

        # Status (short)
        out = _run_git(path, ["status", "--short"])
        r["status"] = out["stdout"] if out["success"] else ""
        r["has_uncommitted"] = bool(r["status"])

        # Recent commits
        out = _run_git(path, ["log", f"--since={since}", "--oneline", "-20"])
        r["recent_commits"] = out["stdout"].split("\n") if out["success"] and out["stdout"] else []
        r["commit_count"] = len(r["recent_commits"])

        # Remote
        out = _run_git(path, ["remote", "-v"])
        r["remote"] = out["stdout"].split("\n")[0] if out["success"] and out["stdout"] else "none"

        # Grep if query specified
        if query:
            out = _run_git(path, ["grep", "-n", "--count", query])
            r["grep_results"] = out["stdout"] if out["success"] else ""

        results[name] = r

    total_commits = sum(r.get("commit_count", 0) for r in results.values())
    repos_with_changes = sum(1 for r in results.values() if r.get("has_uncommitted"))

    return {
        "repos_scanned": len(results),
        "total_commits_found": total_commits,
        "repos_with_uncommitted_changes": repos_with_changes,
        "repos": results,
    }


def write_branch(repo: str, branch_name: str) -> dict:
    """Create a new branch in a repo. Requires L3 + WRITE_LOCK."""
    path = _resolve_repo(repo)
    if not path:
        return {"success": False, "error": f"Unknown repo: {repo}"}

    out = _run_git(path, ["checkout", "-b", branch_name])
    if not out["success"]:
        return {"success": False, "error": out["stderr"]}

    return {
        "success": True,
        "repo": repo,
        "action": "branch",
        "branch_name": branch_name,
        "undo_recipe": {"type": "git_delete_branch", "repo": path, "branch_name": branch_name},
    }


def write_commit(repo: str, message: str, files: list[str] = None) -> dict:
    """Commit changes in a repo. Requires L3 + WRITE_LOCK."""
    path = _resolve_repo(repo)
    if not path:
        return {"success": False, "error": f"Unknown repo: {repo}"}

    # Stage files
    if files:
        for f in files:
            out = _run_git(path, ["add", f])
            if not out["success"]:
                return {"success": False, "error": f"Failed to stage {f}: {out['stderr']}"}
    else:
        out = _run_git(path, ["add", "-A"])
        if not out["success"]:
            return {"success": False, "error": f"Failed to stage: {out['stderr']}"}

    # Commit
    out = _run_git(path, ["commit", "-m", message])
    if not out["success"]:
        return {"success": False, "error": out["stderr"]}

    # Get commit SHA
    sha_out = _run_git(path, ["rev-parse", "HEAD"])
    sha = sha_out["stdout"] if sha_out["success"] else "unknown"

    return {
        "success": True,
        "repo": repo,
        "action": "commit",
        "commit_sha": sha,
        "message": message,
        "undo_recipe": {"type": "git_revert", "repo": path, "commit_sha": sha},
    }


def ship_push(repo: str, branch: str = "") -> dict:
    """Push to remote. Requires L4 + SHIP_LOCK."""
    path = _resolve_repo(repo)
    if not path:
        return {"success": False, "error": f"Unknown repo: {repo}"}

    # Get current branch if not specified
    if not branch:
        out = _run_git(path, ["branch", "--show-current"])
        branch = out["stdout"] if out["success"] else "main"

    # Get current SHA for rollback
    sha_out = _run_git(path, ["rev-parse", "HEAD"])
    previous_sha = sha_out["stdout"] if sha_out["success"] else ""

    out = _run_git(path, ["push", "origin", branch], timeout=60)
    if not out["success"]:
        return {"success": False, "error": out["stderr"]}

    return {
        "success": True,
        "repo": repo,
        "action": "push",
        "branch_pushed": branch,
        "previous_sha": previous_sha,
        "undo_recipe": {"type": "service_rollback", "repo": path, "previous_sha": previous_sha},
    }


# Discover repos on import
discover_repos()
