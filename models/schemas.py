"""Pydantic request/response/internal models for joao-spine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ── Request Models ──────────────────────────────────────────────────────────

class DispatchRequest(BaseModel):
    session_name: str = Field(..., description="tmux session name")
    command: str = Field(..., description="Shell command to execute")
    wait: bool = Field(False, description="Wait for output before returning")


class AudioRequest(BaseModel):
    audio_url: str = Field(..., description="URL to audio file")
    context: str = Field("", description="Optional context for processing")


class MeetingRequest(BaseModel):
    transcript: str = Field(..., description="Meeting transcript text")
    participants: list[str] = Field(default_factory=list)
    context: str = Field("", description="Optional context")


class VisionRequest(BaseModel):
    image_url: str = Field(..., description="URL to image")
    prompt: str = Field("Describe this image and extract key information.", description="Vision prompt")


class TextRequest(BaseModel):
    text: str = Field(..., description="Text content to process")
    context: str = Field("", description="Optional context")


# ── Response Models ─────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "joao-spine"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SubCheck(BaseModel):
    ok: bool
    latency_ms: float | None = None
    error: str | None = None


class SshCheck(SubCheck):
    target: str | None = None


class TmuxCheck(SubCheck):
    sessions: list[str] = Field(default_factory=list)


class StatusChecks(BaseModel):
    supabase: SubCheck
    ssh: SshCheck
    tmux: TmuxCheck


class StatusResponse(BaseModel):
    status: str  # "healthy" | "degraded" | "down"
    service: str = "joao-spine"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str | None = None
    uptime_seconds: float
    checks: StatusChecks
    recent_activity: list[dict[str, Any]] = Field(default_factory=list)


class DispatchResponse(BaseModel):
    request_id: str | None = None
    session_name: str
    command: str
    status: str
    output: str | None = None


class ContentResponse(BaseModel):
    source: str
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    idea_vault_id: str | None = None


# ── Voice Models ───────────────────────────────────────────────────────────

class VoiceIntent(BaseModel):
    intent: str = Field(..., description="dispatch | status | check | idea | unknown")
    agent: str | None = None
    task: str | None = None
    priority: str = "normal"
    project: str | None = None


class VoiceCommandResponse(BaseModel):
    transcript: str
    intent: VoiceIntent
    result: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Internal Models ─────────────────────────────────────────────────────────

class AIResult(BaseModel):
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)


class IdeaVaultRecord(BaseModel):
    source: str
    title: str
    content: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionLogRecord(BaseModel):
    endpoint: str
    action: str
    input_summary: str
    output_summary: str
    status: str
    duration_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentOutputRecord(BaseModel):
    session_name: str
    command: str
    output: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Council Dispatch Models ────────────────────────────────────────────────

class CouncilDispatchRequest(BaseModel):
    agent: str = Field(..., description="Council agent name (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA)")
    task: str = Field(..., description="Task description for the agent")
    priority: str = Field("normal", description="Priority: normal, urgent, critical")
    context: str | None = Field(None, description="Optional context")
    project: str | None = Field(None, description="Project name")


class CouncilDispatchResponse(BaseModel):
    status: str
    agent: str
    task_preview: str
    timestamp: str
    server_response: dict[str, Any] = Field(default_factory=dict)


class DispatchLogRecord(BaseModel):
    agent: str
    task: str
    priority: str = "normal"
    project: str | None = None
    status: str = "dispatched"
    session: str | None = None


# ── Context / Log Models (joao-interface) ─────────────────────────────────

class ContextResponse(BaseModel):
    context: str
    session_log: str
    last_updated: str


class LogEntry(BaseModel):
    role: str = Field(..., description="user or assistant")
    content: str = Field(..., description="Log message content")
    timestamp: str | None = Field(None, description="ISO timestamp, auto-filled if omitted")


class LogResponse(BaseModel):
    status: str = "logged"


# ── Chat Proxy Models ─────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., description="user or assistant")
    content: str | list[dict] = Field(..., description="Message content (text or multimodal content blocks)")


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., description="Conversation messages")
    session_id: str = Field("default", description="Session identifier")
    model: str = Field("haiku", description="Model: 'haiku' (default) or 'sonnet'")
    mode: str = Field("joao", description="Chat mode: 'joao' (default) or 'mrdp'")


# ── QA Pipeline Models ───────────────────────────────────────────────────

class QASubmission(BaseModel):
    dispatch_id: str = Field(..., description="ID from dispatch_log")
    agent: str = Field(..., description="Agent that completed the work (BYTE, ARIA, etc.)")
    task_summary: str = Field(..., description="What was the task")
    code_diff: str | None = Field(None, description="Git diff of changes")
    files_changed: list[str] = Field(default_factory=list, description="List of files modified")
    test_results: str | None = Field(None, description="Test output")


class QAReview(BaseModel):
    model: str = Field(..., description="Reviewer model (sonnet, gpt-4o, opus)")
    score: int = Field(..., description="Score 1-10")
    verdict: str = Field(..., description="pass, fail, or needs_revision")
    feedback: str = Field("", description="Reviewer feedback")


class QAConsensus(BaseModel):
    dispatch_id: str
    reviews: list[QAReview] = Field(default_factory=list)
    consensus_verdict: str = Field(..., description="deploy, review, or reject")
    avg_score: float = Field(0.0)
    deploy_ready: bool = Field(False)


# ── Briefing Email Models ─────────────────────────────────────────────────────

class BriefingEmailRequest(BaseModel):
    subject: str = Field(..., description="Email subject line")
    body_html: str = Field(..., description="HTML body of the briefing email")
    to_email: str | None = Field(None, description="Recipient address; falls back to SENDGRID_TO env var")
    from_name: str = Field("JOAO", description="Sender display name")

