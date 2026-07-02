"""Shared response envelopes (error contract)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(..., examples=["MODE_NOT_FOUND"])
    message: str = Field(..., examples=["The requested mode does not exist."])
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Uniform error envelope — Python exceptions are never exposed."""

    success: bool = False
    error: ErrorBody
