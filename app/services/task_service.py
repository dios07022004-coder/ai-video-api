"""Task lifecycle service: status reads + guarded state transitions."""

from __future__ import annotations

from datetime import datetime

from app.api.errors import ForbiddenError, TaskNotFoundError
from app.config.constants import ErrorCode
from app.database.base import utcnow
from app.database.tables import Partner, Task
from app.models.enums import STATE_PROGRESS_FLOOR, TaskStatus, can_transition, is_terminal
from app.repositories.tasks import TaskRepository
from app.schemas.tasks import TaskStatusResponse
from app.services.billing_service import BillingService


class TaskService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._repo = task_repo

    # ── reads ────────────────────────────────────────────────────────────────
    async def get(self, task_id: str) -> Task:
        task = await self._repo.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(details={"task_id": task_id})
        return task

    async def get_status(self, task_id: str, partner: Partner | None) -> TaskStatusResponse:
        task = await self.get(task_id)
        if partner is not None and task.partner_id not in (None, partner.id):
            # Do not leak existence to other partners.
            raise TaskNotFoundError(details={"task_id": task_id})
        return self.to_status(task)

    async def cancel(
        self, task_id: str, partner: Partner | None, *, billing: BillingService | None
    ) -> TaskStatusResponse:
        """Stop a task from the UI: flip it to CANCELLED and refund the hold.

        Idempotent — cancelling an already-terminal task just echoes its state.
        Best-effort ComfyUI interrupt so a job that is actively running on the
        GPU stops instead of finishing. The worker's own guards (``is_terminal``)
        then no-op the rest of the pipeline.
        """
        task = await self.get(task_id)
        if partner is not None and task.partner_id not in (None, partner.id):
            raise TaskNotFoundError(details={"task_id": task_id})
        if is_terminal(task.status):
            return self.to_status(task)

        await self.mark_cancelled(task)
        if billing is not None and task.partner_id and task.price_credits > 0:
            await billing.refund(
                task.partner_id, task.price_credits, task_id=task.id, note="cancelled by user"
            )
        await self._interrupt_comfy(task)
        return self.to_status(task)

    @staticmethod
    async def _interrupt_comfy(task: Task) -> None:
        """Ask the task's ComfyUI endpoint to interrupt the running prompt.

        Best-effort: any failure is swallowed — the DB cancel already stands and
        the worker will discard the result once it sees the terminal state.
        """
        if not task.comfy_endpoint or ":" not in task.comfy_endpoint:
            return
        try:
            from app.comfy.client import make_client
            from app.comfy.endpoints import Endpoint
            from app.config.settings import get_settings

            host, _, port = task.comfy_endpoint.partition(":")
            ep = Endpoint(host=host, port=int(port or 8188), secure=get_settings().comfy_https)
            client = make_client(ep.base_url, ep.ws_url, suffix="cancel")
            try:
                await client.interrupt()
            finally:
                await client.aclose()
        except Exception:  # noqa: BLE001
            pass

    def to_status(self, task: Task) -> TaskStatusResponse:
        return TaskStatusResponse(
            task_id=task.id,
            status=task.status,
            progress=task.progress,
            result_url=task.result_url,
            error=task.error_message,
            mode=task.mode,
            price_credits=task.price_credits,
            metadata=task.request_metadata,
        )

    # ── transitions (used by the worker pipeline) ────────────────────────────
    async def transition(
        self,
        task: Task,
        target: TaskStatus,
        *,
        progress: int | None = None,
    ) -> Task:
        current = TaskStatus(task.status)
        if current == target:
            pass  # idempotent
        elif not can_transition(current, target):
            # Illegal transition — ignore silently rather than corrupt state, but
            # never move backwards out of a terminal state.
            return task
        else:
            task.status = target.value

        floor = STATE_PROGRESS_FLOOR.get(target, task.progress)
        new_progress = progress if progress is not None else task.progress
        task.progress = max(task.progress, floor, new_progress)
        task.progress = min(100, task.progress)

        if target == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = utcnow()
        self._repo.session.add(task)
        await self._repo.session.flush()
        return task

    async def set_progress(self, task: Task, progress: int) -> Task:
        task.progress = min(100, max(task.progress, progress))
        self._repo.session.add(task)
        await self._repo.session.flush()
        return task

    async def mark_completed(self, task: Task, *, result_url: str, result_path: str) -> Task:
        task.status = TaskStatus.COMPLETED.value
        task.progress = 100
        task.result_url = result_url
        task.result_path = result_path
        task.finished_at = utcnow()
        task.duration_ms = _elapsed_ms(task.started_at, task.finished_at)
        self._repo.session.add(task)
        await self._repo.session.flush()
        return task

    async def mark_failed(self, task: Task, *, code: ErrorCode, message: str) -> Task:
        task.status = TaskStatus.FAILED.value
        task.error_code = code.value
        task.error_message = message
        task.finished_at = utcnow()
        task.duration_ms = _elapsed_ms(task.started_at, task.finished_at)
        self._repo.session.add(task)
        await self._repo.session.flush()
        return task

    async def mark_cancelled(self, task: Task) -> Task:
        task.status = TaskStatus.CANCELLED.value
        task.finished_at = utcnow()
        self._repo.session.add(task)
        await self._repo.session.flush()
        return task

    async def attach_comfy(self, task: Task, *, prompt_id: str, endpoint: str) -> Task:
        task.comfy_prompt_id = prompt_id
        task.comfy_endpoint = endpoint
        self._repo.session.add(task)
        await self._repo.session.flush()
        return task


def _elapsed_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return int((end - start).total_seconds() * 1000)
