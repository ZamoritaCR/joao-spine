"""
TAOP Agent Workforce — Task Queue (SQLite)
Persistent task management that survives restarts.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from core.config import DATABASE_PATH


def _get_db():
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT DEFAULT 'P1',
            status TEXT DEFAULT 'queued',
            output TEXT DEFAULT '',
            error TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}'
        )
    """)
    db.commit()
    return db


def create_task(agent: str, title: str, description: str, priority: str = "P1", metadata: dict = None) -> int:
    """Create a new task and return its ID."""
    db = _get_db()
    cur = db.execute(
        "INSERT INTO tasks (agent, title, description, priority, status, created_at, metadata) VALUES (?, ?, ?, ?, 'queued', ?, ?)",
        (agent.lower(), title, description, priority, datetime.now().isoformat(), json.dumps(metadata or {}))
    )
    db.commit()
    task_id = cur.lastrowid
    db.close()
    return task_id


def get_task(task_id: int) -> dict:
    """Get a single task by ID."""
    db = _get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    if row:
        return dict(row)
    return None


def update_task(task_id: int, **kwargs):
    """Update task fields."""
    db = _get_db()
    allowed = {"status", "output", "error", "tokens_used", "started_at", "completed_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    db.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
    db.commit()
    db.close()


def start_task(task_id: int):
    """Mark task as working."""
    update_task(task_id, status="working", started_at=datetime.now().isoformat())


def complete_task(task_id: int, output: str, tokens_used: int = 0):
    """Mark task as done with output."""
    update_task(task_id, status="done", output=output, tokens_used=tokens_used, completed_at=datetime.now().isoformat())


def fail_task(task_id: int, error: str):
    """Mark task as failed."""
    update_task(task_id, status="failed", error=error, completed_at=datetime.now().isoformat())


def get_queue(status: str = None, agent: str = None, limit: int = 50) -> list:
    """Get tasks filtered by status and/or agent."""
    db = _get_db()
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if agent:
        query += " AND agent = ?"
        params.append(agent.lower())
    query += " ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END, created_at DESC"
    query += f" LIMIT {limit}"
    rows = db.execute(query, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """Get task statistics."""
    db = _get_db()
    total = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    by_status = {}
    for row in db.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status").fetchall():
        by_status[row["status"]] = row["cnt"]
    by_agent = {}
    for row in db.execute("SELECT agent, COUNT(*) as cnt, SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done FROM tasks GROUP BY agent").fetchall():
        by_agent[row["agent"]] = {"total": row["cnt"], "done": row["done"]}
    total_tokens = db.execute("SELECT COALESCE(SUM(tokens_used), 0) FROM tasks").fetchone()[0]
    db.close()
    return {
        "total_tasks": total,
        "by_status": by_status,
        "by_agent": by_agent,
        "total_tokens": total_tokens,
    }


def get_recent(limit: int = 20) -> list:
    """Get most recent tasks regardless of status."""
    db = _get_db()
    rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def delete_task(task_id: int):
    """Delete a task."""
    db = _get_db()
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    db.close()
