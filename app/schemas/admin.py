"""Admin schemas (matches OpenAPI `ModeEditRequest`/`WorkflowUploadRequest`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ModeEditRequest(BaseModel):
    """Editable fields of a mode (admin page). All optional → partial update."""

    name: str | None = None
    category: str | None = None
    enabled: bool | None = None
    workflow: str | None = None
    model: str | None = None
    control_video: str | None = None
    prompt_template: str | None = None
    negative_prompt: str | None = None
    params: dict[str, Any] | None = None


class WorkflowUploadRequest(BaseModel):
    """Upload/edit a workflow (ComfyUI API-format JSON with placeholders)."""

    content: str = Field(..., description="Raw workflow JSON as a string.")
