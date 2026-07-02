"""Generation intake — the write path behind ``POST /generate``.

Owns its own transaction (it must commit the Task + credit hold *before*
enqueuing, so the worker never races a not-yet-committed row). Steps:

    idempotency → resolve mode/model → validate & resolve params →
    price + credit hold → persist Task(queued) → commit → enqueue → return
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from sqlalchemy.exc import IntegrityError

from app.api.errors import AppError, ModeDisabledError, ParamInvalidError
from app.config.constants import ErrorCode, TaskType
from app.config.settings import get_settings
from app.database import Database
from app.database.tables import Task
from app.logging import get_logger
from app.models.definitions import Mode
from app.models.enums import TaskStatus
from app.queue.jobs import enqueue_generation
from app.repositories.billing import BillingRepository
from app.repositories.logs import EventLogRepository
from app.repositories.partners import PartnerRepository
from app.repositories.tasks import TaskRepository
from app.repositories.uploads import UploadRepository
from app.runpod.serverless_client import get_runpod_client
from app.schemas.generation import GenerateRequest, GenerateResponse
from app.services.billing_service import BillingService
from app.services.params import ParamResolver, ResolvedParams
from app.services.task_service import TaskService
from app.workflows.registry import Registry

logger = get_logger("services.generation")


class GenerationService:
    def __init__(self, db: Database, registry: Registry, resolver: ParamResolver | None = None) -> None:
        self._db = db
        self._registry = registry
        self._resolver = resolver or ParamResolver()

    async def create(self, partner_id: int | None, req: GenerateRequest) -> GenerateResponse:
        # Validate mode/params *before* opening the write transaction so bad
        # requests never touch the DB.
        mode = self._registry.get_mode(req.mode)
        if not mode.enabled:
            raise ModeDisabledError(details={"mode": req.mode})
        if req.task_type != mode.task_type:
            raise ParamInvalidError(
                "task_type does not match the mode.",
                details={"requested": req.task_type, "mode_type": mode.task_type},
            )
        model = self._registry.try_get_model(mode.model)
        extra_models = {
            token: self._registry.get_model(mid) for token, mid in mode.model_bindings.items()
        }

        comfy_name = PurePosixPath(req.image_url).name  # provisional; pipeline confirms
        resolved = self._resolver.resolve(
            mode,
            prompt=req.prompt,
            overrides=req.metadata,
            image_comfy_name=comfy_name,
            model=model,
            extra_models=extra_models,
            control_video=mode.control_video,
        )

        price = mode.price_credits

        try:
            task_id, status, reused = await self._persist(partner_id, req, mode, resolved, price)
        except IntegrityError:
            # Concurrent request with the same (partner, request_id) won the insert.
            # Re-read and return it as an idempotent reuse instead of erroring.
            async with self._db.scope() as session:
                existing = await TaskRepository(session).find_idempotent(partner_id, req.request_id)
            if existing is None:
                raise
            return GenerateResponse(task_id=existing.id, status=existing.status, idempotent_reuse=True)

        if not reused:
            backend = get_settings().generation_backend
            if backend == "runpod":
                # Delegate to a RunPod Serverless endpoint; result arrives by webhook.
                status = await self._dispatch_runpod(task_id, mode, req, resolved)
            else:
                # In-pod worker drives ComfyUI directly.
                enqueue_generation(task_id)
                logger.info("generate_enqueued", task_id=task_id, mode=mode.id)
        return GenerateResponse(task_id=task_id, status=status, idempotent_reuse=reused)

    async def _dispatch_runpod(
        self, task_id: str, mode: Mode, req: GenerateRequest, resolved: ResolvedParams
    ) -> str:
        """Send the job to RunPod. On failure, mark the task failed + refund and
        raise so the caller returns a clear infra error to the website."""
        settings = get_settings()
        job_input = {
            "mode": mode.id,
            "prompt": req.prompt,
            "image_url": req.image_url,  # must be publicly reachable by RunPod
            "params": {**(req.metadata or {}), "SEED": resolved.placeholders.get("SEED")},
            "request_id": task_id,
        }
        try:
            job_id = await get_runpod_client().run(job_input, webhook=settings.runpod_webhook_url)
        except AppError as exc:
            await self._fail_dispatch(task_id, code=exc.code, message=exc.message)
            logger.warning("runpod_dispatch_failed", task_id=task_id, code=exc.code.value)
            raise

        async with self._db.scope() as session:
            repo = TaskRepository(session)
            task = await repo.get_by_id(task_id)
            if task is not None:
                await TaskService(repo).attach_comfy(task, prompt_id=job_id, endpoint="runpod")
                await TaskService(repo).transition(task, TaskStatus.LOADING)
                await EventLogRepository(session).record(
                    "runpod_dispatched", source="api", task_id=task_id,
                    partner_id=task.partner_id, data={"job_id": job_id},
                )
                status = task.status
            else:
                status = TaskStatus.LOADING.value
        logger.info("runpod_dispatched", task_id=task_id, job_id=job_id)
        return status

    async def _fail_dispatch(self, task_id: str, *, code: ErrorCode, message: str) -> None:
        async with self._db.scope() as session:
            repo = TaskRepository(session)
            task = await repo.get_by_id(task_id)
            if task is None:
                return
            await TaskService(repo).mark_failed(task, code=code, message=message)
            if task.partner_id and task.price_credits > 0:
                partner_repo = PartnerRepository(session)
                billing = BillingService(BillingRepository(session), partner_repo)
                await billing.refund(task.partner_id, task.price_credits, task_id=task.id,
                                     note="runpod dispatch failed")

    async def _persist(
        self, partner_id, req, mode, resolved, price  # noqa: ANN001
    ) -> tuple[str, str, bool]:
        async with self._db.scope() as session:
            task_repo = TaskRepository(session)

            # Idempotency: same (partner, request_id) reuses the prior task.
            existing = await task_repo.find_idempotent(partner_id, req.request_id)
            if existing is not None:
                logger.info("generate_idempotent_reuse", task_id=existing.id, request_id=req.request_id)
                return existing.id, existing.status, True

            # Refine comfy input name from a known upload, if we have one.
            upload_repo = UploadRepository(session)
            upload = await upload_repo.get_by_url(req.image_url)
            if upload is not None:
                resolved.placeholders["IMAGE"] = upload.comfy_name

            task = Task(
                id=uuid.uuid4().hex,
                partner_id=partner_id,
                user_id=req.user_id,
                request_id=req.request_id,
                task_type=req.task_type.value,
                mode=mode.id,
                status=TaskStatus.QUEUED.value,
                progress=0,
                prompt=resolved.prompt,
                negative_prompt=resolved.negative,
                image_url=req.image_url,
                callback_url=req.callback_url,
                resolved_params=resolved.placeholders,
                request_metadata=req.metadata,
                price_credits=price,
            )
            await task_repo.add(task)

            # Reserve credits (raises InsufficientCreditsError → rolls back).
            if partner_id is not None and price > 0:
                partner_repo = PartnerRepository(session)
                partner = await partner_repo.get_by_id(partner_id)
                if partner is not None:
                    billing = BillingService(BillingRepository(session), partner_repo)
                    await billing.hold(partner, price, task_id=task.id)

            await EventLogRepository(session).record(
                "task_created",
                source="api",
                task_id=task.id,
                partner_id=partner_id,
                data={"mode": mode.id, "price": price},
            )
            task_id = task.id
            status = task.status
        # committed here
        return task_id, status, False
