"""RunPod Serverless handler.

Job input contract (what your website's API sends in the ``input`` field):

    {
      "mode": "image_to_video",         # required — must exist in config/modes
      "prompt": "a running horse",      # optional
      "image": "<base64>",              # image bytes as base64 (data-uri or raw)
      "image_url": "https://…",         # OR a URL to fetch (one of image/image_url)
      "params": { "STEPS": 30, ... },   # optional per-request overrides
      "request_id": "abc123"            # optional — echoed back for your tracking
    }

Job output:

    {
      "status": "COMPLETED",
      "mode": "image_to_video",
      "request_id": "abc123",
      "seed": 12345,
      "duration_ms": 41230,
      "delivery": "url" | "base64",
      "video_url": "https://…"          # when S3/R2 configured
      # or "video_base64": "...", "content_type": "video/mp4"
    }

On failure it returns ``{"error": {"code", "message"}}`` — never a raw traceback.
Users/credits live on YOUR server; this endpoint only turns params into a video.
"""

from __future__ import annotations

import base64
import binascii
import time
from pathlib import PurePosixPath

import httpx

from app.api.errors import AppError, ParamInvalidError, UploadInvalidError
from app.comfy.client import ComfyClient, make_client
from app.comfy.engine import WorkflowEngine
from app.config.constants import ErrorCode
from app.config.settings import get_settings
from app.logging import configure_logging, get_logger
from app.serverless.storage import deliver
from app.services.params import ParamResolver
from app.workflows.registry import get_registry

logger = get_logger("serverless.handler")

_engine = WorkflowEngine()
_resolver = ParamResolver()
_settings = get_settings()

configure_logging(level=_settings.log_level, json_output=_settings.log_json, service="serverless")


async def process(inp: dict) -> dict:
    """Core generation logic (framework-agnostic; unit-testable without RunPod)."""
    started = time.perf_counter()
    registry = get_registry()

    mode_id = (inp or {}).get("mode")
    if not mode_id:
        raise ParamInvalidError("Field 'mode' is required.")
    mode = registry.get_mode(mode_id)
    if not mode.enabled:
        raise ParamInvalidError("Mode is disabled.", details={"mode": mode_id})

    model = registry.try_get_model(mode.model)
    extra_models = {tok: registry.get_model(mid) for tok, mid in mode.model_bindings.items()}

    resolved = _resolver.resolve(
        mode,
        prompt=inp.get("prompt"),
        overrides=inp.get("params"),
        model=model,
        extra_models=extra_models,
        control_video=mode.control_video,
    )

    client: ComfyClient = make_client(
        _settings.comfy_base_url, _settings.comfy_ws_url, suffix="serverless",
        timeout=_settings.comfy_timeout_seconds,
    )
    try:
        if not await client.is_alive():
            raise AppError(
                "ComfyUI is not ready in the worker.",
                code=ErrorCode.COMFY_UNAVAILABLE,
                http_status=503,
            )

        # Stage the input image into ComfyUI.
        image_bytes = await _load_image_bytes(inp)
        if image_bytes is not None:
            name = await client.upload_image_bytes(image_bytes, name="input.png")
            resolved.placeholders["IMAGE"] = name

        # Stage control video if the mode uses one and it's bundled in the image.
        if mode.control_video:
            cv = _settings.control_dir / mode.control_video
            if cv.exists():
                cv_name = await client.upload_image(cv, name=mode.control_video)
                resolved.placeholders["CONTROL_VIDEO"] = cv_name

        # Render the graph and submit.
        graph = registry.loader.load(mode.workflow)
        rendered = _engine.render(
            graph, resolved.placeholders, allow_missing=_optional_tokens(resolved.placeholders)
        )
        prompt_id = await client.submit(rendered)
        logger.info("serverless_submitted", mode=mode_id, prompt_id=prompt_id)

        result = await _await_result(client, prompt_id)
        output = result.primary
        if output is None:
            raise AppError("No output produced.", code=ErrorCode.COMFY_EXECUTION_FAILED, http_status=502)

        data = await client.download(output)
        ext = PurePosixPath(output.filename).suffix.lstrip(".") or "mp4"
        content_type = "video/mp4" if ext == "mp4" else f"video/{ext}"
        delivery = deliver(data, ext=ext, content_type=content_type)

        return {
            "status": "COMPLETED",
            "mode": mode_id,
            "request_id": inp.get("request_id"),
            "seed": resolved.placeholders.get("SEED"),
            "duration_ms": int((time.perf_counter() - started) * 1000),
            **delivery,
        }
    finally:
        await client.aclose()


async def handler(job: dict) -> dict:
    """RunPod entrypoint. Wraps ``process`` and converts errors to a safe body."""
    inp = job.get("input") or {}
    try:
        return await process(inp)
    except AppError as exc:
        logger.warning("serverless_failed", code=exc.code.value, message=exc.message)
        return {"error": {"code": exc.code.value, "message": exc.message, "details": exc.details}}
    except Exception as exc:  # noqa: BLE001
        logger.exception("serverless_crashed")
        return {"error": {"code": ErrorCode.INTERNAL_ERROR.value, "message": "Unexpected worker error."}}


# ── helpers ──────────────────────────────────────────────────────────────────
async def _load_image_bytes(inp: dict) -> bytes | None:
    raw_b64 = inp.get("image")
    if raw_b64:
        payload = raw_b64.split(",", 1)[1] if raw_b64.startswith("data:") else raw_b64
        try:
            return base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise UploadInvalidError("image is not valid base64.", details={"error": str(exc)}) from exc

    url = inp.get("image_url")
    if url:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPError as exc:
            raise UploadInvalidError("Failed to fetch image_url.", details={"error": str(exc)}) from exc
    return None


def _optional_tokens(params: dict) -> set[str]:
    optional = {"LORA", "VAE", "CONTROLNET", "IPADAPTER", "CONTROL_VIDEO"}
    return {t for t in optional if not params.get(t)}


async def _await_result(client: ComfyClient, prompt_id: str):
    import asyncio

    loop = asyncio.get_event_loop()
    deadline = loop.time() + _settings.comfy_timeout_seconds
    while True:
        history = await client.history(prompt_id)
        if prompt_id in history:
            result = client.parse_result(prompt_id, history)  # raises on comfy error
            if result.outputs:
                return result
        if loop.time() > deadline:
            await client.interrupt()
            raise AppError(
                "Generation timed out.", code=ErrorCode.GENERATION_TIMEOUT, http_status=504,
                details={"prompt_id": prompt_id},
            )
        await asyncio.sleep(_settings.comfy_poll_interval)
