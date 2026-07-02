"""Generation request/response schemas (matches OpenAPI `GenerateRequest`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config.constants import TaskType


class GenerateRequest(BaseModel):
    task_type: TaskType = Field(default=TaskType.VIDEO)
    mode: str = Field(..., min_length=1, max_length=120)
    image_url: str = Field(..., description="URL returned by POST /uploads (or an allowed external URL).")
    user_id: str = Field(..., min_length=1, max_length=128)
    request_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Idempotency key. Re-sending the same (partner, request_id) reuses the task.",
    )
    prompt: str | None = Field(default=None, max_length=8000)
    callback_url: str | None = Field(default=None, max_length=2048)
    # Free-form per-request overrides (seed, steps, cfg, sampler, …) validated
    # against the mode's declared param schema in the service layer.
    metadata: dict[str, Any] | None = Field(default=None)

    @field_validator("mode")
    @classmethod
    def _norm_mode(cls, v: str) -> str:
        return v.strip()

    @field_validator("callback_url")
    @classmethod
    def _validate_callback(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("callback_url must be an absolute http(s) URL")
        return v


class GenerateResponse(BaseModel):
    task_id: str
    status: str
    idempotent_reuse: bool = False
