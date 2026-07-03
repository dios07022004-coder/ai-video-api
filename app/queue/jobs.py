"""Job enqueue helpers.

The service layer calls ``enqueue_generation(task_id)`` after persisting a Task.
The job target is referenced **by dotted path** (not by importing the worker
module) to avoid an import cycle between the web app and the worker pipeline.
"""

from __future__ import annotations

from rq.job import Job

from app.config.settings import get_settings
from app.queue.broker import get_queue

# Dotted path to the worker entrypoint (imported lazily by the RQ worker).
GENERATION_JOB = "app.workers.pipeline.run_generation_job"


def enqueue_generation(task_id: str) -> Job:
    settings = get_settings()
    queue = get_queue()
    return queue.enqueue(
        GENERATION_JOB,
        task_id,
        job_id=f"gen-{task_id}",  # RQ job ids allow only [A-Za-z0-9_-]; idempotent per task
        retry=None,               # retries are handled inside the pipeline (domain-aware)
        result_ttl=settings.job_result_ttl_seconds,
        failure_ttl=settings.job_result_ttl_seconds,
        job_timeout=settings.job_timeout_seconds,
        description=f"generate:{task_id}",
    )
