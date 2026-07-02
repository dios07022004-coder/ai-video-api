"""Maintenance jobs (cleanup). Schedule via rq-scheduler / cron / RunPod cron.

``cleanup_old_results`` removes generated artifacts and uploads past the
retention window so disk never fills on a long-running pod.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config.settings import get_settings
from app.database import get_database, session_scope
from app.logging import configure_logging, get_logger
from app.repositories.tasks import TaskRepository
from app.storage import get_storage

logger = get_logger("workers.maintenance")


def cleanup_old_results() -> dict[str, Any]:
    settings = get_settings()
    configure_logging(
        level=settings.log_level, json_output=settings.log_json, log_dir=settings.log_dir, service="worker"
    )
    return asyncio.run(_cleanup())


async def _cleanup() -> dict[str, Any]:
    settings = get_settings()
    storage = get_storage()
    results_removed = await storage.cleanup(
        older_than_days=settings.result_retention_days, subdir=settings.results_subdir
    )
    uploads_removed = await storage.cleanup(
        older_than_days=settings.result_retention_days, subdir=settings.uploads_subdir
    )
    summary = {"results_removed": results_removed, "uploads_removed": uploads_removed}
    logger.info("maintenance_cleanup", **summary)
    return summary


def reconcile_runpod_tasks() -> dict[str, Any]:
    """Recover tasks whose RunPod webhook was lost by polling /status.

    Schedule every few minutes (cron / rq-scheduler) when using the RunPod
    backend. A RunPod /status body has the same {id,status,output} shape the
    webhook posts, so we feed it straight into WebhookService for one code path.
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level, json_output=settings.log_json, log_dir=settings.log_dir, service="worker"
    )
    return asyncio.run(_reconcile())


async def _reconcile(*, stuck_after_seconds: int = 300) -> dict[str, Any]:
    from app.runpod.serverless_client import get_runpod_client
    from app.services.webhook_service import WebhookService

    checked = recovered = 0
    async with session_scope() as session:
        tasks = await TaskRepository(session).list_stuck_runpod(older_than_seconds=stuck_after_seconds)
        stuck = [(t.id, t.comfy_prompt_id) for t in tasks]

    client = get_runpod_client()
    webhook = WebhookService(get_database(), get_storage())
    for task_id, job_id in stuck:
        checked += 1
        try:
            body = await client.status(job_id)
            if body.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
                await webhook.process(body)
                recovered += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconcile_failed", task_id=task_id, job_id=job_id, error=str(exc))

    summary = {"checked": checked, "recovered": recovered}
    logger.info("maintenance_reconcile", **summary)
    return summary


if __name__ == "__main__":
    cleanup_old_results()
