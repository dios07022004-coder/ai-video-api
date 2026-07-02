"""GET /tasks/{task_id} — poll task status/progress/result."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import OptionalPartnerDep, TaskServiceDep
from app.schemas.tasks import TaskStatusResponse

router = APIRouter(tags=["generation"])


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task(
    task_id: str,
    service: TaskServiceDep,
    partner: OptionalPartnerDep,
) -> TaskStatusResponse:
    return await service.get_status(task_id, partner)
