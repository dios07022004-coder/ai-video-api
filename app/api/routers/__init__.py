"""API routers, one module per resource group."""

from fastapi import APIRouter

from app.api.routers import admin, billing, generation, meta, modes, runpod, tasks, uploads


def build_api_router() -> APIRouter:
    """Compose all resource routers into the root API router."""
    root = APIRouter()
    root.include_router(uploads.router)
    root.include_router(generation.router)
    root.include_router(tasks.router)
    root.include_router(modes.router)
    root.include_router(billing.router)
    root.include_router(admin.router)
    root.include_router(runpod.router)
    root.include_router(meta.router)
    return root
