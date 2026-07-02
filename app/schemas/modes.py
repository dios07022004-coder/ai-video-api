"""Mode listing + preview schemas (matches OpenAPI `ModeInfo`/`Preview*`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ModeInfo(BaseModel):
    id: str
    name: str
    category: str
    enabled: bool = True
    model: str
    control_video: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class PreviewRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=8000)


class PreviewResponse(BaseModel):
    mode: str
    prompt: str
    negative: str
    params: dict[str, Any] = Field(default_factory=dict)
