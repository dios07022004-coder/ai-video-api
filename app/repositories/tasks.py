"""Task persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.database.tables import Task
from app.models.enums import TaskStatus
from app.repositories.base import BaseRepository

_NON_TERMINAL = (
    TaskStatus.QUEUED.value,
    TaskStatus.LOADING.value,
    TaskStatus.PREPARING.value,
    TaskStatus.RUNNING.value,
    TaskStatus.ENCODING.value,
)


class TaskRepository(BaseRepository[Task]):
    model = Task

    async def get_by_id(self, task_id: str) -> Task | None:
        return await self.session.get(Task, task_id)

    async def get_by_comfy_prompt_id(self, prompt_id: str) -> Task | None:
        """Find a task by its backend job id (ComfyUI prompt id or RunPod job id)."""
        stmt = select(Task).where(Task.comfy_prompt_id == prompt_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_idempotent(self, partner_id: int | None, request_id: str | None) -> Task | None:
        """Return an existing task for the same (partner, request_id), if any."""
        if request_id is None:
            return None
        stmt = select(Task).where(
            Task.partner_id == partner_id, Task.request_id == request_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_partner(
        self, partner_id: int, *, limit: int = 100, offset: int = 0
    ) -> Sequence[Task]:
        stmt = (
            select(Task)
            .where(Task.partner_id == partner_id)
            .order_by(Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_stuck_runpod(self, *, older_than_seconds: int, limit: int = 100) -> Sequence[Task]:
        """Non-terminal RunPod-dispatched tasks with no update for a while — used
        by the reconciler to recover from a dropped webhook."""
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        stmt = (
            select(Task)
            .where(
                Task.comfy_endpoint == "runpod",
                Task.status.in_(_NON_TERMINAL),
                Task.comfy_prompt_id.is_not(None),
                Task.updated_at < cutoff,
            )
            .order_by(Task.updated_at.asc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_by_status(self, status: TaskStatus, *, limit: int = 200) -> Sequence[Task]:
        stmt = (
            select(Task)
            .where(Task.status == status.value)
            .order_by(Task.created_at.asc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
