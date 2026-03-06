"""joao_chat — send a message to JOAO, get response via Claude."""

from __future__ import annotations

import os
from pathlib import Path

import anthropic

_MASTER_CONTEXT = Path("/home/zamoritacr/joao-spine/JOAO_MASTER_CONTEXT.md")
_SESSION_LOG = Path("/home/zamoritacr/joao-spine/JOAO_SESSION_LOG.md")

_MODEL = "claude-sonnet-4-6"


def _load_system_prompt() -> str:
    parts = []
    if _MASTER_CONTEXT.exists():
        parts.append(_MASTER_CONTEXT.read_text(encoding="utf-8"))
    parts.append(
        "\nYou are JOAO — Johan's AI exocortex. You have full context above. "
        "Be direct. No fluff. No emojis. Match Johan's energy."
    )
    return "\n\n".join(parts)


async def joao_chat(message: str, context: str = "") -> str:
    """Send a message to JOAO and get a response via Claude Sonnet.

    Args:
        message: The message or question for JOAO
        context: Optional additional context to include
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "Error: ANTHROPIC_API_KEY not set"

    system = _load_system_prompt()
    user_content = message
    if context:
        user_content = f"Context: {context}\n\n{message}"

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        reply = "".join(b.text for b in response.content if b.type == "text")

        # Log to session log
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        entry = f"\n## MCP CHAT — {ts}\n\n**Johan:** {message}\n\n**JOAO:** {reply}\n"
        try:
            with _SESSION_LOG.open("a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass

        return reply
    except Exception as e:
        return f"Error: {e}"
