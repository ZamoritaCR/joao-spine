"""
Undo Executor -- reverses capability operations using stored recipes.

Implements all 5 undo types from the JOAO OS spec:
- delete_artifacts: remove job artifact directory
- git_revert: git revert <sha> on a repo
- git_delete_branch: git branch -D <branch>
- noop: read-only operation, nothing to undo
- service_rollback: checkout previous SHA + restart service
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from capability.artifact_store import ARTIFACTS_DIR

logger = logging.getLogger(__name__)


def execute_undo(recipe: dict, autonomy_level: str = "L2") -> dict:
    """Execute an undo recipe.

    Args:
        recipe: Undo recipe dict with 'type' and type-specific fields
        autonomy_level: Current autonomy level (must be >= original operation's level)

    Returns:
        dict with: success, type, details
    """
    undo_type = recipe.get("type", "noop")

    executors = {
        "noop": _undo_noop,
        "delete_artifacts": _undo_delete_artifacts,
        "git_revert": _undo_git_revert,
        "git_delete_branch": _undo_git_delete_branch,
        "service_rollback": _undo_service_rollback,
    }

    executor = executors.get(undo_type)
    if not executor:
        return {"success": False, "type": undo_type, "details": f"Unknown undo type: {undo_type}"}

    try:
        result = executor(recipe)
        logger.info("undo_executed: type=%s success=%s", undo_type, result.get("success"))
        return result
    except Exception as e:
        logger.error("undo_failed: type=%s error=%s", undo_type, str(e))
        return {"success": False, "type": undo_type, "details": str(e)}


def _undo_noop(recipe: dict) -> dict:
    return {"success": True, "type": "noop", "details": "Read-only operation, nothing to undo."}


def _undo_delete_artifacts(recipe: dict) -> dict:
    target = recipe.get("target", "")
    if not target:
        return {"success": False, "type": "delete_artifacts", "details": "No target directory specified"}

    # If target is a job_id, resolve it
    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = ARTIFACTS_DIR / target

    # Safety: must be under ARTIFACTS_DIR
    resolved = target_path.resolve()
    if not str(resolved).startswith(str(ARTIFACTS_DIR.resolve())):
        return {
            "success": False,
            "type": "delete_artifacts",
            "details": f"Refusing to delete outside artifacts dir: {target}",
        }

    if not resolved.exists():
        return {"success": True, "type": "delete_artifacts", "details": f"Already removed: {target}"}

    shutil.rmtree(resolved)
    return {"success": True, "type": "delete_artifacts", "details": f"Removed: {resolved}"}


def _undo_git_revert(recipe: dict) -> dict:
    repo = recipe.get("repo", "")
    commit_sha = recipe.get("commit_sha", "")
    if not repo or not commit_sha:
        return {"success": False, "type": "git_revert", "details": "Missing repo or commit_sha"}

    try:
        result = subprocess.run(
            ["git", "revert", "--no-edit", commit_sha],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"success": False, "type": "git_revert", "details": result.stderr.strip()}
        return {
            "success": True, "type": "git_revert",
            "details": f"Reverted {commit_sha[:8]} in {repo}",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "type": "git_revert", "details": "Git revert timed out"}


def _undo_git_delete_branch(recipe: dict) -> dict:
    repo = recipe.get("repo", "")
    branch = recipe.get("branch_name", "")
    if not repo or not branch:
        return {"success": False, "type": "git_delete_branch", "details": "Missing repo or branch_name"}

    # Refuse to delete main/master
    if branch in ("main", "master"):
        return {"success": False, "type": "git_delete_branch", "details": "Refusing to delete main/master"}

    try:
        result = subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=repo, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"success": False, "type": "git_delete_branch", "details": result.stderr.strip()}
        return {
            "success": True, "type": "git_delete_branch",
            "details": f"Deleted branch {branch} in {repo}",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "type": "git_delete_branch", "details": "Branch delete timed out"}


def _undo_service_rollback(recipe: dict) -> dict:
    repo = recipe.get("repo", "")
    previous_sha = recipe.get("previous_sha", "")
    service = recipe.get("service", "")
    if not repo or not previous_sha:
        return {"success": False, "type": "service_rollback", "details": "Missing repo or previous_sha"}

    try:
        # Checkout previous SHA
        result = subprocess.run(
            ["git", "checkout", previous_sha],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"success": False, "type": "service_rollback", "details": f"Checkout failed: {result.stderr.strip()}"}

        # Restart service if specified
        if service:
            svc_result = subprocess.run(
                ["systemctl", "--user", "restart", service],
                capture_output=True, text=True, timeout=30,
            )
            if svc_result.returncode != 0:
                return {
                    "success": False, "type": "service_rollback",
                    "details": f"Checkout OK, service restart failed: {svc_result.stderr.strip()}",
                }

        return {
            "success": True, "type": "service_rollback",
            "details": f"Rolled back to {previous_sha[:8]}" + (f", restarted {service}" if service else ""),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "type": "service_rollback", "details": "Rollback timed out"}
