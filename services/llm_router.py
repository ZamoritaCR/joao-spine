"""
LLMRouter - JOAO intelligent model routing.
Ollama local now. Set OPENROUTER_API_KEY in .env to switch to cloud. Zero code changes needed.
"""
import os
import logging
from typing import Optional, AsyncGenerator
from openai import AsyncOpenAI

logger = logging.getLogger("joao.llm_router")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
USE_OPENROUTER = os.getenv("USE_OPENROUTER", "").strip().lower() in {"1", "true", "yes", "on"} and bool(OPENROUTER_API_KEY)

OLLAMA_MODELS = {
    "code_generation":  "qwen2.5-coder:latest",
    "code_review":      "deepseek-coder-v2:latest",
    "reasoning":        "llama3.1:8b",
    "summarization":    "llama3.1:8b",
    "classification":   "llama3.1:8b",
    "chat":             "llama3.1:8b",
    "council_dispatch": "llama3.1:8b",
    "bulk_processing":  "deepseek-coder-v2:latest",
    "fallback":         "llama3.1:8b",
}

OPENROUTER_MODELS = {
    "code_generation":  "qwen/qwen3-coder-480b:free",
    "code_review":      "deepseek/deepseek-r1:free",
    "reasoning":        "deepseek/deepseek-r1:free",
    "summarization":    "meta-llama/llama-3.3-70b-instruct:free",
    "classification":   "google/gemma-3-12b-it:free",
    "chat":             "meta-llama/llama-3.3-70b-instruct:free",
    "council_dispatch": "meta-llama/llama-3.3-70b-instruct:free",
    "bulk_processing":  "deepseek/deepseek-v3.2",
    "fallback":         "meta-llama/llama-3.3-70b-instruct:free",
}

def _client() -> AsyncOpenAI:
    if USE_OPENROUTER:
        return AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1", default_headers={"HTTP-Referer":"https://joao.theartofthepossible.io","X-Title":"JOAO-TAOP"})
    return AsyncOpenAI(api_key="ollama", base_url="http://localhost:11434/v1")

def resolve_model(task_type: str) -> str:
    m = OPENROUTER_MODELS if USE_OPENROUTER else OLLAMA_MODELS
    return m.get(task_type, m["fallback"])

async def complete(messages: list, task_type: str = "fallback", model: str = None, temperature: float = 0.3, max_tokens: int = 2048) -> str:
    c = _client()
    m = model or resolve_model(task_type)
    r = await c.chat.completions.create(model=m, messages=messages, temperature=temperature, max_tokens=max_tokens)
    return r.choices[0].message.content or ""

async def stream_complete(messages: list, task_type: str = "chat", model: str = None, temperature: float = 0.3, max_tokens: int = 2048) -> AsyncGenerator[str, None]:
    c = _client()
    m = model or resolve_model(task_type)
    stream = await c.chat.completions.create(model=m, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=True)
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta

async def summarize(text: str, context: str = "") -> str:
    msgs = [{"role":"system","content":"Precise summarizer. Concise. Factual. No filler."},{"role":"user","content":f"{context}\n\nSummarize:\n{text}" if context else f"Summarize:\n{text}"}]
    return await complete(msgs, task_type="summarization", max_tokens=512)

async def classify(text: str, categories: list) -> str:
    cats = ", ".join(categories)
    msgs = [{"role":"system","content":f"Classify into exactly one of: {cats}. Reply with only the category name."},{"role":"user","content":text}]
    return (await complete(msgs, task_type="classification", max_tokens=20, temperature=0.0)).strip()

async def generate_code(prompt: str, language: str = "python") -> str:
    msgs = [{"role":"system","content":f"Expert {language} engineer. Clean production code. No explanations unless asked."},{"role":"user","content":prompt}]
    return await complete(msgs, task_type="code_generation", max_tokens=1024, temperature=0.1)

async def reason(prompt: str, context: str = "") -> str:
    msgs = [{"role":"system","content":"Precise analytical reasoner. Step by step. Factual. Concise."}]
    msgs.append({"role":"user","content":f"Context:\n{context}\n\nTask:\n{prompt}" if context else prompt})
    return await complete(msgs, task_type="reasoning", max_tokens=512)

async def council_task(agent_name: str, task: str, context: str = "") -> str:
    msgs = [{"role":"system","content":f"You are {agent_name}, expert AI agent on the JOAO Council at The Art of The Possible. Complete tasks with precision. No filler."},{"role":"user","content":f"Context:\n{context}\n\nTask:\n{task}" if context else task}]
    return await complete(msgs, task_type="council_dispatch", max_tokens=512)

async def health_check() -> dict:
    try:
        r = await complete([{"role":"user","content":"Reply with only the word: HEALTHY"}], task_type="classification", max_tokens=10, temperature=0.0)
        return {"status":"ok","provider":"openrouter" if USE_OPENROUTER else "ollama","model":resolve_model("classification"),"response":r.strip()}
    except Exception as e:
        return {"status":"error","provider":"openrouter" if USE_OPENROUTER else "ollama","error":str(e)}
