"""Upload schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    image_url: str = Field(..., description="Absolute URL of the stored, validated image.")
