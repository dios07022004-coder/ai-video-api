"""Processes RunPod Serverless completion webhooks.

RunPod POSTs the finished job to ``/runpod/webhook/{secret}``. This service:
  * matches the job to our Task (by RunPod job id, or by echoed request_id),
  * stores the result (a URL if RunPod uploaded to S3/R2, or decodes inline base64),
  * commits the credit charge (success) or refunds the hold (failure),
  * fires the website's ``callback_url``.

It owns its own transaction and is idempotent — a duplicate webhook for an
already-terminal task is ignored.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from app.config.constants import ErrorCode
from app.database import Database
from app.logging import get_logger
from app.models.enums import is_terminal
from app.repositories.billing import BillingRepository
from app.repositories.logs import EventLogRepository
from app.repositories.partners import PartnerRepository
from app.repositories.tasks import TaskRepository
from app.services.billing_service import BillingService
from app.services.callback_service import CallbackService
from app.services.task_service import TaskService
from app.storage.base import StorageBackend

logger = get_logger("services.webhook")


class WebhookService:
    def __init__(self, db: Database, storage: StorageBackend) -> None:
        self._db = db
        self._storage = storage

    async def process(self, body: dict[str, Any]) -> dict[str, Any]:
        job_id = body.get("id")
        rp_status = body.get("status")
        output = body.get("output") or {}
        # RunPod wraps our handler's dict in `output`; errors may be at either level.
        error = output.get("error") or body.get("error")

        callback: dict[str, Any] | None = None

        async with self._db.scope() as session:
            repo = TaskRepository(session)
            task = None
            if job_id:
                task = await repo.get_by_comfy_prompt_id(job_id)
            if task is None and output.get("request_id"):
                task = await repo.get_by_id(output["request_id"])
            if task is None:
                logger.warning("webhook_task_not_found", job_id=job_id)
                return {"ok": True, "matched": False}
            if is_terminal(task.status):
                return {"ok": True, "duplicate": True}

            svc = TaskService(repo)
            has_result = bool(output.get("video_url") or output.get("video_base64"))
            success = rp_status == "COMPLETED" and error is None and has_result

            if success:
                result_url, result_path = await self._store(task.id, output)
                await svc.mark_completed(task, result_url=result_url, result_path=result_path)
                if task.partner_id and task.price_credits > 0:
                    billing = BillingService(BillingRepository(session), PartnerRepository(session))
                    await billing.commit_charge(task.partner_id, task.price_credits, task_id=task.id)
                await EventLogRepository(session).record(
                    "task_completed", source="runpod", task_id=task.id, partner_id=task.partner_id,
                    data={"result_url": result_url, "job_id": job_id},
                )
                callback = {
                    "url": task.callback_url, "status": "completed", "result_url": result_url,
                    "duration_ms": task.duration_ms, "credits": task.price_credits,
                    "metadata": task.request_metadata, "task_id": task.id,
                }
            else:
                message = (error or {}).get("message") if isinstance(error, dict) else (error or "Generation failed on RunPod.")
                await svc.mark_failed(task, code=ErrorCode.COMFY_EXECUTION_FAILED, message=str(message))
                if task.partner_id and task.price_credits > 0:
                    billing = BillingService(BillingRepository(session), PartnerRepository(session))
                    await billing.refund(task.partner_id, task.price_credits, task_id=task.id, note="runpod failed")
                await EventLogRepository(session).record(
                    "task_failed", level="ERROR", source="runpod", task_id=task.id,
                    partner_id=task.partner_id, message=str(message), data={"job_id": job_id},
                )
                callback = {
                    "url": task.callback_url, "status": "failed", "result_url": None,
                    "duration_ms": None, "credits": 0,
                    "metadata": task.request_metadata, "task_id": task.id,
                }

        # Fire the website callback outside the transaction.
        if callback and callback["url"]:
            await CallbackService().fire(
                callback["url"], task_id=callback["task_id"], status=callback["status"],
                result_url=callback["result_url"], duration_ms=callback["duration_ms"],
                credits=callback["credits"], metadata=callback["metadata"],
            )
        return {"ok": True}

    async def _store(self, task_id: str, output: dict[str, Any]) -> tuple[str, str]:
        """Return (result_url, result_path). If RunPod already uploaded to S3 we
        keep its URL; if it returned base64 we persist it to our own storage."""
        url = output.get("video_url")
        if url:
            return url, ""  # already hosted (S3/R2/CDN)

        b64 = output.get("video_base64")
        ext = (output.get("format") or "mp4").lstrip(".")
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ValueError(f"invalid base64 in webhook output: {exc}") from exc
        stored = await self._storage.save_result(data, ext=ext, task_id=task_id)
        return stored.url, str(stored.path)
