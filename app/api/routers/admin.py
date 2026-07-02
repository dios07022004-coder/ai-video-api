"""Admin API: mode CRUD, control-video upload, workflow management.

All endpoints require the admin token (``X-Admin-Token`` or ``Authorization``).
Mutations validate before writing and hot-update the registry snapshot so changes
take effect immediately.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, UploadFile

from app.api.deps import AdminDep, RegistryDep
from app.api.errors import UploadInvalidError
from app.config.constants import VIDEO_EXTENSIONS
from app.config.settings import get_settings
from app.logging import get_logger
from app.schemas.admin import ModeEditRequest, WorkflowUploadRequest
from app.security.sanitize import safe_filename

logger = get_logger("api.admin")
router = APIRouter(tags=["admin"])


# ── Modes ────────────────────────────────────────────────────────────────────
@router.get("/admin/modes/{mode_id}")
async def admin_get_mode(mode_id: str, _admin: AdminDep, registry: RegistryDep) -> dict[str, Any]:
    return registry.raw_mode(mode_id)


@router.put("/admin/modes/{mode_id}")
async def admin_put_mode(
    mode_id: str,
    payload: ModeEditRequest,
    _admin: AdminDep,
    registry: RegistryDep,
) -> dict[str, Any]:
    """Partial update: merge provided fields over the current mode JSON."""
    try:
        current = registry.raw_mode(mode_id)
    except Exception:
        current = {"id": mode_id}
    patch = payload.model_dump(exclude_none=True)
    current.update(patch)
    mode = registry.upsert_mode_file(mode_id, current)
    return mode.model_dump()


@router.post("/admin/modes/{mode_id}/control-video")
async def admin_upload_control_video(
    mode_id: str,
    _admin: AdminDep,
    registry: RegistryDep,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    settings = get_settings()
    name = safe_filename(file.filename or "control.mp4", default="control.mp4")
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in VIDEO_EXTENSIONS:
        raise UploadInvalidError(
            "Unsupported control-video type.", details={"allowed": sorted(VIDEO_EXTENSIONS)}
        )
    settings.control_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.control_dir / f"{mode_id}__{name}"
    dest.write_bytes(await file.read())

    # Bind it to the mode.
    current = registry.raw_mode(mode_id) if _mode_exists(registry, mode_id) else {"id": mode_id}
    current["control_video"] = dest.name
    registry.upsert_mode_file(mode_id, current)
    logger.info("control_video_uploaded", mode=mode_id, file=dest.name)
    return {"mode": mode_id, "control_video": dest.name, "path": str(dest)}


def _mode_exists(registry: RegistryDep, mode_id: str) -> bool:  # type: ignore[valid-type]
    try:
        registry.raw_mode(mode_id)
        return True
    except Exception:
        return False


# ── Workflows ────────────────────────────────────────────────────────────────
@router.get("/admin/workflows")
async def admin_list_workflows(_admin: AdminDep, registry: RegistryDep) -> dict[str, Any]:
    names = registry.loader.list_names()
    return {"workflows": names, "count": len(names)}


@router.get("/admin/workflows/{name}")
async def admin_get_workflow(name: str, _admin: AdminDep, registry: RegistryDep) -> dict[str, Any]:
    graph = registry.loader.load(name)
    from app.comfy.engine import WorkflowEngine

    placeholders = sorted(WorkflowEngine().discover(graph))
    return {"name": name, "placeholders": placeholders, "content": graph}


@router.put("/admin/workflows/{name}")
async def admin_save_workflow(
    name: str,
    payload: WorkflowUploadRequest,
    _admin: AdminDep,
    registry: RegistryDep,
) -> dict[str, Any]:
    path = registry.loader.save(name, payload.content)
    graph = registry.loader.load(name)
    from app.comfy.engine import WorkflowEngine

    placeholders = sorted(WorkflowEngine().discover(graph))
    return {"name": name, "saved": True, "path": str(path), "placeholders": placeholders}
