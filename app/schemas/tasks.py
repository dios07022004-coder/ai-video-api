"""Task status schema (matches OpenAPI `TaskStatus`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: int = Field(default=0, ge=0, le=100)
    result_url: str | None = None
    error: str | None = None
    mode: str
    price_credits: int = 0
    metadata: dict[str, Any] | None = None
