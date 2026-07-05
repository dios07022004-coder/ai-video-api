"""Task status + control: poll, and cancel a running/queued generation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import BillingServiceDep, OptionalPartnerDep, TaskServiceDep
from app.schemas.tasks import TaskStatusResponse

router = APIRouter(tags=["generation"])


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task(
    task_id: str,
    service: TaskServiceDep,
    partner: OptionalPartnerDep,
) -> TaskStatusResponse:
    return await service.get_status(task_id, partner)


@router.post("/tasks/{task_id}/cancel", response_model=TaskStatusResponse)
async def cancel_task(
    task_id: str,
    service: TaskServiceDep,
    billing: BillingServiceDep,
    partner: OptionalPartnerDep,
) -> TaskStatusResponse:
    """Stop a queued/running task and refund the credit hold (idempotent)."""
    return await service.cancel(task_id, partner, billing=billing)
