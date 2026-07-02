"""FastAPI application factory + ASGI entrypoint.

Wires middleware, exception handlers, routers, static file serving (dev) and a
lifespan that bootstraps directories, the DB schema and the config registry, then
probes for ComfyUI/GPU in the background (non-blocking startup).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.errors import install_exception_handlers
from app.api.routers import build_api_router
from app.config.settings import get_settings
from app.database import get_database
from app.logging import configure_logging, get_logger
from app.middleware import AccessLogMiddleware, RequestContextMiddleware
from app.runpod.health import detect_gpu, wait_for_comfy
from app.workflows.registry import get_registry

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = get_settings()
    configure_logging(
        level=settings.log_level, json_output=settings.log_json, log_dir=settings.log_dir, service="api"
    )
    settings.ensure_directories()

    # Bootstrap schema (dev/SQLite). Production runs migrations, create_all is a no-op then.
    await get_database().create_all()

    # Load modes/models/workflows.
    summary = get_registry().reload()
    logger.info("startup_registry", **{k: v for k, v in summary.items() if k != "errors"})

    # Probe GPU + ComfyUI in the background so the API is ready immediately.
    # Skipped when this host only orchestrates (runpod backend has no local GPU).
    async def _probe() -> None:
        if settings.generation_backend == "runpod":
            logger.info("orchestrator_mode", backend="runpod", comfy_probe="skipped")
            return
        gpu = await detect_gpu()
        logger.info("gpu_detected", available=gpu.available, name=gpu.name, vram_total_mb=gpu.vram_total_mb)
        await wait_for_comfy(timeout=settings.comfy_timeout_seconds if settings.env == "prod" else 30)

    task = asyncio.create_task(_probe())
    try:
        yield
    finally:
        task.cancel()
        await get_database().dispose()
        logger.info("shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ai-video-api",
        version=__version__,
        description="Production AI Video Generation API over ComfyUI (RunPod).",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Middleware (order: context → access log → CORS).
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.env != "prod" else [settings.public_base_url],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_exception_handlers(app)
    app.include_router(build_api_router())

    # Serve stored files in dev (NGINX handles this in production).
    settings.ensure_directories()
    app.mount("/files", StaticFiles(directory=str(settings.data_dir)), name="files")

    _customize_openapi(app)
    return app


def _customize_openapi(app: FastAPI) -> None:
    from fastapi.openapi.utils import get_openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        schema.setdefault("components", {}).setdefault("securitySchemes", {}).update(
            {
                "ApiKey": {"type": "apiKey", "in": "header", "name": get_settings().api_key_header},
                "AdminToken": {"type": "apiKey", "in": "header", "name": get_settings().admin_token_header},
            }
        )
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


app = create_app()


def run() -> None:
    """Console-script entrypoint (`aivideo-api`)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.env == "dev",
        log_config=None,  # we own logging
    )


if __name__ == "__main__":
    run()
