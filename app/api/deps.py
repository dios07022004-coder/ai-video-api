"""FastAPI dependency-injection wiring.

Each request gets: a transactional DB session, an authenticated partner (from the
API key), and freshly-constructed service objects bound to that session. Services
never outlive the request. Singletons (registry, storage, redis) are shared.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ForbiddenError, UnauthorizedError
from app.config.settings import Settings, get_settings
from app.database import Database, get_database, get_session
from app.database.tables import Partner
from app.repositories.billing import BillingRepository
from app.repositories.partners import PartnerRepository
from app.repositories.tasks import TaskRepository
from app.repositories.uploads import UploadRepository
from app.security.files import ImageValidator
from app.security.keys import hash_api_key, verify_admin_token
from app.security.ratelimit import RateLimiter
from app.services.billing_service import BillingService
from app.services.generation_service import GenerationService
from app.services.mode_service import ModeService
from app.services.task_service import TaskService
from app.services.upload_service import UploadService
from app.services.webhook_service import WebhookService
from app.storage import StorageBackend, get_storage
from app.workflows.registry import Registry, get_registry

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def registry_dep() -> Registry:
    return get_registry()


RegistryDep = Annotated[Registry, Depends(registry_dep)]


def storage_dep() -> StorageBackend:
    return get_storage()


StorageDep = Annotated[StorageBackend, Depends(storage_dep)]


def database_dep() -> Database:
    return get_database()


DatabaseDep = Annotated[Database, Depends(database_dep)]


# ── Authentication ───────────────────────────────────────────────────────────
async def _lookup_partner(session: AsyncSession, api_key: str | None) -> Partner | None:
    if not api_key:
        return None
    key_hash = hash_api_key(api_key.strip())
    repo = PartnerRepository(session)
    found = await repo.get_by_api_key_hash(key_hash)
    if found is None:
        return None
    partner, key = found
    await repo.touch_key(key)
    return partner


async def require_partner(
    session: SessionDep,
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Partner:
    partner = await _lookup_partner(session, x_api_key)
    if partner is None:
        raise UnauthorizedError()
    return partner


async def optional_partner(
    session: SessionDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Partner | None:
    return await _lookup_partner(session, x_api_key)


PartnerDep = Annotated[Partner, Depends(require_partner)]
OptionalPartnerDep = Annotated[Partner | None, Depends(optional_partner)]


async def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> bool:
    """Admin auth accepts either X-Admin-Token or an Authorization header
    (``Bearer <token>`` or the raw token) — both are in the published spec."""
    candidate = x_admin_token
    if not candidate and authorization:
        candidate = authorization.removeprefix("Bearer ").strip()
    if not verify_admin_token(candidate):
        raise ForbiddenError("Invalid admin token.")
    return True


AdminDep = Annotated[bool, Depends(require_admin)]


# ── Rate limiting (per partner / per IP) ─────────────────────────────────────
async def enforce_rate_limit(request: Request, settings: SettingsDep) -> None:
    from app.api.errors import RateLimitedError
    from app.queue.broker import get_async_redis

    limiter = RateLimiter(get_async_redis(), limit_per_minute=settings.rate_limit_per_minute)
    api_key = request.headers.get(settings.api_key_header)
    identity = f"key:{hash_api_key(api_key)[:16]}" if api_key else f"ip:{request.client.host if request.client else 'unknown'}"
    allowed, remaining = await limiter.check(identity)
    request.state.rate_remaining = remaining
    if not allowed:
        raise RateLimitedError(details={"limit_per_minute": settings.rate_limit_per_minute})


RateLimitDep = Depends(enforce_rate_limit)


# ── Service factories (request-scoped) ───────────────────────────────────────
def upload_service(session: SessionDep, storage: StorageDep, settings: SettingsDep) -> UploadService:
    return UploadService(UploadRepository(session), ImageValidator(settings), storage)


def task_service(session: SessionDep) -> TaskService:
    return TaskService(TaskRepository(session))


def mode_service(registry: RegistryDep) -> ModeService:
    return ModeService(registry)


def billing_service(session: SessionDep) -> BillingService:
    return BillingService(BillingRepository(session), PartnerRepository(session))


def generation_service(database: DatabaseDep, registry: RegistryDep) -> GenerationService:
    return GenerationService(database, registry)


def webhook_service(database: DatabaseDep, storage: StorageDep) -> WebhookService:
    return WebhookService(database, storage)


UploadServiceDep = Annotated[UploadService, Depends(upload_service)]
TaskServiceDep = Annotated[TaskService, Depends(task_service)]
ModeServiceDep = Annotated[ModeService, Depends(mode_service)]
BillingServiceDep = Annotated[BillingService, Depends(billing_service)]
GenerationServiceDep = Annotated[GenerationService, Depends(generation_service)]
WebhookServiceDep = Annotated[WebhookService, Depends(webhook_service)]
