"""Modes: list, preview, and admin reload."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import AdminDep, ModeServiceDep, RegistryDep
from app.config.constants import TaskType
from app.schemas.modes import ModeInfo, PreviewRequest, PreviewResponse

router = APIRouter(tags=["modes"])


@router.get("/modes", response_model=list[ModeInfo])
async def list_modes(
    service: ModeServiceDep,
    task_type: TaskType = Query(default=TaskType.VIDEO),
) -> list[ModeInfo]:
    return service.list_modes(task_type)


@router.post("/modes/{mode_id}/preview", response_model=PreviewResponse)
async def preview_mode(
    mode_id: str,
    payload: PreviewRequest,
    service: ModeServiceDep,
) -> PreviewResponse:
    return service.preview(mode_id, payload.prompt)


@router.post("/admin/modes/reload", tags=["modes"])
async def reload_modes(_admin: AdminDep, registry: RegistryDep) -> dict[str, Any]:
    """Hot-reload modes/models/workflows from disk (no restart, no deploy)."""
    return registry.reload()
