"""TAOP Agent Workforce — Configuration"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "ZamoritaCR")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Database ──────────────────────────────────────────────
DATABASE_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).parent.parent / "taop_tasks.db"))

# ── Models ────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-20250514"
OPENAI_MODEL = "gpt-4o"
GEMINI_MODEL = "gemini-2.0-flash"

# ── Limits ────────────────────────────────────────────────
MAX_TOKENS = 4096
TASK_TIMEOUT = 300  # seconds
