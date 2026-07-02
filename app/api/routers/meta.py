"""GET /health — liveness/readiness + GPU/queue telemetry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.runpod.health import collect_health

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> dict[str, Any]:
    report = await collect_health()
    return report.to_dict()
