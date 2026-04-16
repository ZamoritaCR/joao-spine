"""JOAO intelligent model routing with Ollama primary, OpenRouter fallback, and Claude on demand."""

from __future__ import annotations

import logging
import os
from typing import AsyncGenerator

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

logger = logging.getLogger("joao.llm_router")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
USE_OPENROUTER = bool(OPENROUTER_API_KEY)
USE_CLAUDE = bool(ANTHROPIC_API_KEY)

OLLAMA_MODELS = {
    "code_generation": "qwen2.5-coder:latest",
    "code_review": "deepseek-coder-v2:latest",
    "reasoning": "llama3.1:8b",
    "summarization": "llama3.1:8b",
    "classification": "llama3.1:8b",
    "chat": "llama3.1:8b",
    "council_dispatch": "llama3.1:8b",
    "bulk_processing": "deepseek-coder-v2:latest",
    "fallback": "llama3.1:8b",
}

OPENROUTER_MODELS = {
    "code_generation": "qwen/qwen3-coder-480b:free",
    "code_review": "deepseek/deepseek-r1:free",
    "reasoning": "deepseek/deepseek-r1:free",
    "summarization": "meta-llama/llama-3.3-70b-instruct:free",
    "classification": "google/gemma-3-12b-it:free",
    "chat": "meta-llama/llama-3.3-70b-instruct:free",
    "council_dispatch": "meta-llama/llama-3.3-70b-instruct:free",
    "bulk_processing": "deepseek/deepseek-v3.2",
    "fallback": "meta-llama/llama-3.3-70b-instruct:free",
}

CLAUDE_MODELS = {
    "chat": "claude-sonnet-4-6",
    "reasoning": "claude-opus-4-6",
    "fallback": "claude-3-5-haiku-latest",
}


def _normalize_model(requested_model: str | None) -> str:
    return (requested_model or "").strip().lower()


def _stringify_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def resolve_model(task_type: str, provider: str = "ollama") -> str:
    if provider == "openrouter":
        models = OPENROUTER_MODELS
    elif provider == "claude":
        models = CLAUDE_MODELS
    else:
        models = OLLAMA_MODELS
    return models.get(task_type, models["fallback"])


def select_provider(task_type: str = "chat", requested_model: str | None = None) -> tuple[str, str]:
    normalized = _normalize_model(requested_model)

    if not normalized or normalized in {"auto", "default", "local", "joao"}:
        return "ollama", resolve_model(task_type, "ollama")

    if normalized in {"ollama", "local-ollama"}:
        return "ollama", resolve_model(task_type, "ollama")
    if normalized in {"openrouter", "cloud"}:
        return "openrouter", resolve_model(task_type, "openrouter")
    if normalized in {"claude", "sonnet"}:
        return "claude", CLAUDE_MODELS["chat"]
    if normalized == "opus":
        return "claude", CLAUDE_MODELS["reasoning"]
    if normalized == "haiku":
        return "claude", CLAUDE_MODELS["fallback"]

    if normalized.startswith("claude-"):
        return "claude", requested_model.strip()
    if normalized.startswith("ollama:"):
        return "ollama", requested_model.split(":", 1)[1].strip()
    if normalized.startswith("openrouter:"):
        return "openrouter", requested_model.split(":", 1)[1].strip()

    if requested_model in OLLAMA_MODELS.values():
        return "ollama", requested_model
    if requested_model in OPENROUTER_MODELS.values():
        return "openrouter", requested_model
    if requested_model in CLAUDE_MODELS.values():
        return "claude", requested_model

    if "/" in normalized:
        return "openrouter", requested_model
    if ":" in normalized:
        return "ollama", requested_model

    return "ollama", requested_model


def _openai_client(provider: str) -> AsyncOpenAI:
    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY not configured")
        return AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://joao.theartofthepossible.io",
                "X-Title": "JOAO-TAOP",
            },
        )
    return AsyncOpenAI(api_key="ollama", base_url="http://localhost:11434/v1")


def _claude_client() -> AsyncAnthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    return AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=120.0)


def _split_system_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    converted: list[dict] = []

    for message in messages:
        role = str(message.get("role", "user"))
        content = _stringify_content(message.get("content", ""))
        if role == "system":
            system_parts.append(content)
            continue
        converted.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            }
        )

    if not converted:
        converted = [{"role": "user", "content": "Hello."}]

    return "\n\n".join(part for part in system_parts if part), converted


async def _complete_with_provider(
    provider: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> str:
    if provider == "claude":
        system_prompt, anthropic_messages = _split_system_messages(messages)
        response = await _claude_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=anthropic_messages,
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )

    response = await _openai_client(provider).chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


async def complete(
    messages: list[dict],
    task_type: str = "fallback",
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    provider, resolved = select_provider(task_type=task_type, requested_model=model)
    try:
        return await _complete_with_provider(provider, resolved, messages, temperature, max_tokens)
    except Exception as exc:
        if provider == "ollama" and OPENROUTER_API_KEY:
            fallback_model = resolve_model(task_type, "openrouter")
            logger.warning("Ollama failed for %s, falling back to OpenRouter: %s", resolved, exc)
            return await _complete_with_provider(
                "openrouter",
                fallback_model,
                messages,
                temperature,
                max_tokens,
            )
        raise


async def _stream_with_provider(
    provider: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    if provider == "claude":
        system_prompt, anthropic_messages = _split_system_messages(messages)
        async with _claude_client().messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=anthropic_messages,
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield text
        return

    stream = await _openai_client(provider).chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


async def stream_complete(
    messages: list[dict],
    task_type: str = "chat",
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> AsyncGenerator[str, None]:
    provider, resolved = select_provider(task_type=task_type, requested_model=model)
    try:
        async for chunk in _stream_with_provider(provider, resolved, messages, temperature, max_tokens):
            yield chunk
    except Exception as exc:
        if provider == "ollama" and OPENROUTER_API_KEY:
            fallback_model = resolve_model(task_type, "openrouter")
            logger.warning(
                "Ollama streaming failed for %s, falling back to OpenRouter: %s",
                resolved,
                exc,
            )
            async for chunk in _stream_with_provider(
                "openrouter",
                fallback_model,
                messages,
                temperature,
                max_tokens,
            ):
                yield chunk
            return
        raise


async def summarize(text: str, context: str = "") -> str:
    messages = [
        {"role": "system", "content": "Precise summarizer. Concise. Factual. No filler."},
        {
            "role": "user",
            "content": f"{context}\n\nSummarize:\n{text}" if context else f"Summarize:\n{text}",
        },
    ]
    return await complete(messages, task_type="summarization", max_tokens=512)


async def classify(text: str, categories: list[str]) -> str:
    cats = ", ".join(categories)
    messages = [
        {
            "role": "system",
            "content": f"Classify into exactly one of: {cats}. Reply with only the category name.",
        },
        {"role": "user", "content": text},
    ]
    return (
        await complete(messages, task_type="classification", max_tokens=20, temperature=0.0)
    ).strip()


async def generate_code(prompt: str, language: str = "python") -> str:
    messages = [
        {
            "role": "system",
            "content": f"Expert {language} engineer. Clean production code. No explanations unless asked.",
        },
        {"role": "user", "content": prompt},
    ]
    return await complete(messages, task_type="code_generation", max_tokens=1024, temperature=0.1)


async def reason(prompt: str, context: str = "") -> str:
    messages = [{"role": "system", "content": "Precise analytical reasoner. Step by step. Factual. Concise."}]
    messages.append(
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nTask:\n{prompt}" if context else prompt,
        }
    )
    return await complete(messages, task_type="reasoning", max_tokens=512)


async def council_task(agent_name: str, task: str, context: str = "") -> str:
    messages = [
        {
            "role": "system",
            "content": (
                f"You are {agent_name}, expert AI agent on the JOAO Council at "
                "The Art of The Possible. Complete tasks with precision. No filler."
            ),
        },
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nTask:\n{task}" if context else task,
        },
    ]
    return await complete(messages, task_type="council_dispatch", max_tokens=512)


async def health_check() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get("http://localhost:11434/api/tags")
            response.raise_for_status()
            models = [model.get("name", "") for model in response.json().get("models", [])]
        return {
            "status": "ok",
            "provider": "ollama",
            "model": resolve_model("chat", "ollama"),
            "response": "HEALTHY",
            "available_models": models,
            "openrouter_fallback": bool(OPENROUTER_API_KEY),
            "claude_available": bool(ANTHROPIC_API_KEY),
        }
    except Exception as ollama_error:
        if OPENROUTER_API_KEY:
            try:
                probe = await complete(
                    [{"role": "user", "content": "Reply with only the word: HEALTHY"}],
                    task_type="classification",
                    model="openrouter",
                    max_tokens=10,
                    temperature=0.0,
                )
                return {
                    "status": "ok",
                    "provider": "openrouter",
                    "model": resolve_model("chat", "openrouter"),
                    "response": probe.strip(),
                    "fallback_from": "ollama",
                    "claude_available": bool(ANTHROPIC_API_KEY),
                    "warning": f"Ollama unavailable: {ollama_error}",
                }
            except Exception as openrouter_error:
                return {
                    "status": "error",
                    "provider": "ollama",
                    "error": (
                        f"Ollama unavailable: {ollama_error}; "
                        f"OpenRouter fallback failed: {openrouter_error}"
                    ),
                    "claude_available": bool(ANTHROPIC_API_KEY),
                }
        return {
            "status": "error",
            "provider": "ollama",
            "error": str(ollama_error),
            "claude_available": bool(ANTHROPIC_API_KEY),
        }
