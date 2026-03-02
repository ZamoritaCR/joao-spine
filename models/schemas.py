"""Pydantic request/response/internal models for joao-spine."""

from __future__ import annotations

from datetime import datetime
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
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class StatusResponse(BaseModel):
    status: str = "ok"
    uptime_seconds: float
    recent_activity: list[dict[str, Any]] = Field(default_factory=list)


class DispatchResponse(BaseModel):
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
