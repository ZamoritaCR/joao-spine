"""
TAOP Agent Workforce — Engine
Routes tasks to the right agent → right API → returns results.
"""
import time
import traceback
from datetime import datetime

import anthropic
import openai
import google.generativeai as genai

from core.config import (
    ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY,
    CLAUDE_MODEL, OPENAI_MODEL, GEMINI_MODEL, MAX_TOKENS,
)
from core.agents import AGENTS, get_agent, find_best_agent
from core.tasks import create_task, start_task, complete_task, fail_task, get_task


# ── Initialize API Clients ─────────────────────────────────

_claude_client = None
_openai_client = None
_gemini_configured = False


def _get_claude():
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _claude_client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _init_gemini():
    global _gemini_configured
    if not _gemini_configured:
        genai.configure(api_key=GOOGLE_API_KEY)
        _gemini_configured = True


# ── API Calls ──────────────────────────────────────────────

def _call_claude(system_prompt: str, user_message: str) -> tuple[str, int]:
    """Call Claude API. Returns (response_text, tokens_used)."""
    client = _get_claude()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    text = response.content[0].text
    tokens = response.usage.input_tokens + response.usage.output_tokens
    return text, tokens


def _call_openai(system_prompt: str, user_message: str) -> tuple[str, int]:
    """Call OpenAI API. Returns (response_text, tokens_used)."""
    client = _get_openai()
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    text = response.choices[0].message.content
    tokens = response.usage.total_tokens
    return text, tokens


def _call_gemini(system_prompt: str, user_message: str) -> tuple[str, int]:
    """Call Gemini API. Returns (response_text, estimated_tokens)."""
    _init_gemini()
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=system_prompt,
    )
    response = model.generate_content(user_message)
    text = response.text
    # Gemini doesn't always return token counts cleanly, estimate
    tokens = len(system_prompt.split()) + len(user_message.split()) + len(text.split())
    return text, tokens


# ── Engine Map ─────────────────────────────────────────────

ENGINE_MAP = {
    "claude": _call_claude,
    "openai": _call_openai,
    "gemini": _call_gemini,
}


# ── Core Dispatch ──────────────────────────────────────────

def dispatch(agent_name: str, task_description: str, title: str = None, priority: str = "P1", context: str = "") -> dict:
    """
    Dispatch a task to an agent.
    
    Args:
        agent_name: Agent key (e.g., "aria", "max") or "auto" for auto-routing
        task_description: What the agent should do
        title: Short title for the task (auto-generated if not provided)
        priority: P0/P1/P2/P3
        context: Additional context to append to the task
    
    Returns:
        dict with task_id, agent, status, output
    """
    # Auto-route if needed
    if agent_name.lower() == "auto":
        agent_name = find_best_agent(task_description)
    
    agent = get_agent(agent_name)
    if not agent:
        return {"error": f"Unknown agent: {agent_name}", "status": "failed"}
    
    # Generate title if not provided
    if not title:
        title = task_description[:80] + ("..." if len(task_description) > 80 else "")
    
    # Create task in queue
    task_id = create_task(
        agent=agent_name,
        title=title,
        description=task_description,
        priority=priority,
        metadata={"context": context} if context else {},
    )
    
    # Execute
    start_task(task_id)
    
    try:
        # Build the full prompt
        full_message = task_description
        if context:
            full_message = f"CONTEXT:\n{context}\n\nTASK:\n{task_description}"
        
        # Call the right API
        engine_fn = ENGINE_MAP.get(agent["engine"])
        if not engine_fn:
            raise ValueError(f"Unknown engine: {agent['engine']}")
        
        output, tokens = engine_fn(agent["system_prompt"], full_message)
        
        complete_task(task_id, output=output, tokens_used=tokens)
        
        return {
            "task_id": task_id,
            "agent": agent["name"],
            "agent_key": agent_name,
            "badge": agent["badge"],
            "engine": agent["engine"],
            "status": "done",
            "output": output,
            "tokens_used": tokens,
            "title": title,
        }
    
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        fail_task(task_id, error=error_msg)
        return {
            "task_id": task_id,
            "agent": agent["name"],
            "agent_key": agent_name,
            "status": "failed",
            "error": str(e),
            "output": "",
        }


def dispatch_to_team(task_description: str, agents: list = None, priority: str = "P1", context: str = "") -> list:
    """
    Dispatch the same task to multiple agents (fan-out).
    Useful for getting multiple perspectives.
    
    Args:
        task_description: What to do
        agents: List of agent keys. If None, sends to aria, max, gemma.
        priority: P0/P1/P2/P3
        context: Additional context
    
    Returns:
        List of results from each agent
    """
    if agents is None:
        agents = ["aria", "max", "gemma"]
    
    results = []
    for agent_name in agents:
        result = dispatch(agent_name, task_description, priority=priority, context=context)
        results.append(result)
    
    return results


def quick_ask(agent_name: str, question: str) -> str:
    """Quick question to an agent — returns just the text response."""
    result = dispatch(agent_name, question, priority="P2")
    if result.get("status") == "done":
        return result["output"]
    return f"ERROR: {result.get('error', 'Unknown error')}"
