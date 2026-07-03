"""The generation pipeline — executed by an RQ worker for each task.

RQ jobs are synchronous; ``run_generation_job`` is the sync entrypoint that runs
the async pipeline via ``asyncio.run``. State is persisted in *short* transactions
(never one long-held lock across a 30-minute generation).

Flow (mirrors TASK STATES):
    queued → loading → preparing → running → encoding → completed | failed
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from app.api.errors import AppError, ComfyExecutionError, GenerationTimeoutError
from app.comfy.client import ComfyClient, ComfyResult
from app.comfy.endpoints import EndpointPool
from app.comfy.engine import WorkflowEngine
from app.config.constants import ErrorCode
from app.config.settings import get_settings
from app.database import get_database, session_scope
from app.database.tables import Task
from app.logging import bind_context, configure_logging, get_logger
from app.models.enums import TaskStatus, is_terminal
from app.repositories.billing import BillingRepository
from app.repositories.logs import EventLogRepository
from app.repositories.partners import PartnerRepository
from app.repositories.tasks import TaskRepository
from app.services.billing_service import BillingService
from app.services.callback_service import CallbackService
from app.services.task_service import TaskService
from app.workflows.registry import get_registry

logger = get_logger("workers.pipeline")

_PROGRESS_FLUSH_INTERVAL = 1.5  # seconds between DB progress writes


# ── RQ entrypoint ────────────────────────────────────────────────────────────
def run_generation_job(task_id: str) -> dict[str, Any]:
    """Synchronous RQ target. Delegates to the async pipeline."""
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        json_output=settings.log_json,
        log_dir=settings.log_dir,
        service="worker",
    )
    # RQ runs every job in a fresh event loop. The async DB engine (asyncpg pool)
    # is a process-wide singleton bound to the loop it was first used in, so reusing
    # it across jobs raises "attached to a different loop". Drop the cached engine
    # so a fresh one is built inside THIS job's loop, and dispose it when done.
    get_database.cache_clear()
    return asyncio.run(_run_job(task_id))


async def _run_job(task_id: str) -> dict[str, Any]:
    try:
        return await _execute(task_id)
    finally:
        try:
            await get_database().dispose()
        except Exception:  # noqa: BLE001
            pass


async def _execute(task_id: str) -> dict[str, Any]:
    bind_context(task_id=task_id, component="pipeline")
    engine = WorkflowEngine()
    registry = get_registry()
    settings = get_settings()
    pool = EndpointPool(settings)
    worker_suffix = f"{os.getpid()}"

    # Load & guard the task (short txn).
    async with session_scope() as session:
        task = await TaskRepository(session).get_by_id(task_id)
        if task is None:
            logger.warning("task_missing")
            return {"task_id": task_id, "status": "missing"}
        if is_terminal(task.status):
            logger.info("task_already_terminal", status=task.status)
            return {"task_id": task_id, "status": task.status}
        # snapshot fields we need outside the session
        snapshot = _snapshot(task)
        await _svc(session).transition(task, TaskStatus.LOADING)
        task.attempts += 1

    client: ComfyClient | None = None
    try:
        mode = registry.get_mode(snapshot["mode"])
        graph = registry.loader.load(mode.workflow)

        # Acquire a GPU endpoint (least-loaded, alive).
        endpoint, client = await pool.acquire(worker_suffix=worker_suffix)
        await _set_endpoint(task_id, endpoint.label)

        # Push the input image to the chosen ComfyUI node's input/ dir.
        params = dict(snapshot["resolved_params"])
        await _stage_input_image(client, snapshot["image_url"], params)
        await _stage_control_video(client, mode.control_video, params)

        # preparing → render placeholders into the concrete graph
        await _transition(task_id, TaskStatus.PREPARING)
        rendered = engine.render(graph, params, allow_missing=_optional_tokens(params))

        # submit → running
        prompt_id = await client.submit(rendered)
        await _attach_comfy(task_id, prompt_id, endpoint.label)
        await _transition(task_id, TaskStatus.RUNNING, progress=15)

        # track progress + await completion
        result = await _drive(client, prompt_id, task_id, settings.comfy_timeout_seconds,
                              poll_interval=settings.comfy_poll_interval)

        # encoding → download → store
        await _transition(task_id, TaskStatus.ENCODING, progress=92)
        stored = await _persist_result(client, result, task_id)

        # completed + commit charge + callback
        await _complete(task_id, stored_url=stored[0], stored_path=stored[1], callback=True)
        logger.info("generation_completed", result_url=stored[0])
        return {"task_id": task_id, "status": "completed", "result_url": stored[0]}

    except AppError as exc:
        await _fail(task_id, code=exc.code, message=exc.message)
        logger.warning("generation_failed", code=exc.code.value, message=exc.message)
        return {"task_id": task_id, "status": "failed", "error": exc.code.value}
    except Exception as exc:  # noqa: BLE001 — never crash the worker loop
        await _fail(task_id, code=ErrorCode.INTERNAL_ERROR, message="Unexpected worker error.")
        logger.exception("generation_crashed", error=str(exc))
        return {"task_id": task_id, "status": "failed", "error": "INTERNAL_ERROR"}
    finally:
        if client is not None:
            await client.aclose()


# ── progress driver ──────────────────────────────────────────────────────────
async def _drive(
    client: ComfyClient,
    prompt_id: str,
    task_id: str,
    timeout: float,
    *,
    poll_interval: float,
) -> ComfyResult:
    """Watch WS progress while polling /history for completion. WS is best-effort;
    history polling is authoritative."""
    loop = asyncio.get_event_loop()
    state = {"progress": 15, "last_flush": 0.0}

    async def on_event(evt: dict[str, Any]) -> None:
        etype, data = evt["type"], evt["data"]
        if etype == "progress":
            value, maximum = data.get("value", 0), max(1, data.get("max", 1))
            pct = 15 + int(75 * value / maximum)
            state["progress"] = min(90, max(state["progress"], pct))
        elif etype == "executing" and data.get("node") is not None:
            state["progress"] = min(88, state["progress"] + 1)
        now = loop.time()
        if now - state["last_flush"] >= _PROGRESS_FLUSH_INTERVAL:
            state["last_flush"] = now
            await _set_progress(task_id, state["progress"])

    watch_task = asyncio.create_task(client.watch(prompt_id, on_event))

    deadline = loop.time() + timeout
    try:
        while True:
            history = await client.history(prompt_id)
            if prompt_id in history:
                result = client.parse_result(prompt_id, history)  # raises on comfy error
                if result.outputs:
                    return result
            if loop.time() > deadline:
                await client.interrupt()
                raise GenerationTimeoutError(details={"prompt_id": prompt_id, "timeout": timeout})
            await asyncio.sleep(poll_interval)
    finally:
        watch_task.cancel()
        try:
            await watch_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


# ── staging helpers ──────────────────────────────────────────────────────────
async def _stage_input_image(client: ComfyClient, image_url: str | None, params: dict) -> None:
    if not image_url:
        return
    # Resolve a known local upload → push its bytes into ComfyUI input/.
    async with session_scope() as session:
        from app.repositories.uploads import UploadRepository

        upload = await UploadRepository(session).get_by_url(image_url)
    if upload is not None and Path(upload.stored_path).exists():
        comfy_name = await client.upload_image(Path(upload.stored_path), name=upload.comfy_name)
        params["IMAGE"] = comfy_name
    else:
        # External/unknown URL: leave whatever IMAGE was resolved at intake.
        logger.info("input_image_not_local", image_url=image_url)


async def _stage_control_video(client: ComfyClient, control_video: str | None, params: dict) -> None:
    if not control_video:
        return
    path = get_settings().control_dir / control_video
    if path.exists():
        name = await client.upload_image(path, name=control_video)
        params["CONTROL_VIDEO"] = name


def _optional_tokens(params: dict) -> set[str]:
    optional = {"LORA", "VAE", "CONTROLNET", "IPADAPTER", "CONTROL_VIDEO"}
    return {t for t in optional if not params.get(t)}


# ── persistence helpers (short transactions) ─────────────────────────────────
def _svc(session) -> TaskService:  # noqa: ANN001
    return TaskService(TaskRepository(session))


def _snapshot(task: Task) -> dict[str, Any]:
    return {
        "mode": task.mode,
        "image_url": task.image_url,
        "resolved_params": dict(task.resolved_params or {}),
        "callback_url": task.callback_url,
        "partner_id": task.partner_id,
        "price_credits": task.price_credits,
        "metadata": task.request_metadata,
    }


async def _transition(task_id: str, target: TaskStatus, *, progress: int | None = None) -> None:
    async with session_scope() as session:
        task = await TaskRepository(session).get_by_id(task_id)
        if task and not is_terminal(task.status):
            await _svc(session).transition(task, target, progress=progress)


async def _set_progress(task_id: str, progress: int) -> None:
    async with session_scope() as session:
        task = await TaskRepository(session).get_by_id(task_id)
        if task and not is_terminal(task.status):
            await _svc(session).set_progress(task, progress)


async def _set_endpoint(task_id: str, endpoint: str) -> None:
    async with session_scope() as session:
        task = await TaskRepository(session).get_by_id(task_id)
        if task:
            task.comfy_endpoint = endpoint
            session.add(task)


async def _attach_comfy(task_id: str, prompt_id: str, endpoint: str) -> None:
    async with session_scope() as session:
        task = await TaskRepository(session).get_by_id(task_id)
        if task:
            await _svc(session).attach_comfy(task, prompt_id=prompt_id, endpoint=endpoint)


async def _persist_result(client: ComfyClient, result: ComfyResult, task_id: str) -> tuple[str, str]:
    from app.storage import get_storage

    output = result.primary
    if output is None:
        raise ComfyExecutionError("ComfyUI produced no output artifact.")
    data = await client.download(output)
    ext = Path(output.filename).suffix.lstrip(".") or "mp4"
    stored = await get_storage().save_result(data, ext=ext, task_id=task_id)
    return stored.url, str(stored.path)


async def _complete(task_id: str, *, stored_url: str, stored_path: str, callback: bool) -> None:
    async with session_scope() as session:
        repo = TaskRepository(session)
        task = await repo.get_by_id(task_id)
        if task is None:
            return
        svc = TaskService(repo)
        await svc.mark_completed(task, result_url=stored_url, result_path=stored_path)
        # commit the credit hold as a charge
        if task.partner_id and task.price_credits > 0:
            partner_repo = PartnerRepository(session)
            billing = BillingService(BillingRepository(session), partner_repo)
            await billing.commit_charge(task.partner_id, task.price_credits, task_id=task.id)
        await EventLogRepository(session).record(
            "task_completed", source="worker", task_id=task.id, partner_id=task.partner_id,
            data={"result_url": stored_url, "duration_ms": task.duration_ms},
        )
        cb = task.callback_url
        partner_id = task.partner_id
        price = task.price_credits
        duration = task.duration_ms
        meta = task.request_metadata
        status = task.status
    if callback and cb:
        await CallbackService().fire(
            cb, task_id=task_id, status=status, result_url=stored_url,
            duration_ms=duration, credits=price, metadata=meta,
        )


async def _fail(task_id: str, *, code: ErrorCode, message: str) -> None:
    async with session_scope() as session:
        repo = TaskRepository(session)
        task = await repo.get_by_id(task_id)
        if task is None or is_terminal(task.status):
            return
        svc = TaskService(repo)
        await svc.mark_failed(task, code=code, message=message)
        # refund the credit hold
        if task.partner_id and task.price_credits > 0:
            partner_repo = PartnerRepository(session)
            billing = BillingService(BillingRepository(session), partner_repo)
            await billing.refund(task.partner_id, task.price_credits, task_id=task.id, note="generation failed")
        await EventLogRepository(session).record(
            "task_failed", level="ERROR", source="worker", task_id=task.id,
            partner_id=task.partner_id, message=message, data={"code": code.value},
        )
        cb = task.callback_url
        partner_id = task.partner_id
        price = task.price_credits
        meta = task.request_metadata
    if cb:
        await CallbackService().fire(
            cb, task_id=task_id, status=TaskStatus.FAILED.value, result_url=None,
            duration_ms=None, credits=0, metadata=meta,
        )
