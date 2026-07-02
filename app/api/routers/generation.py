"""POST /generate — enqueue an asynchronous generation task."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import GenerationServiceDep, PartnerDep, RateLimitDep
from app.schemas.generation import GenerateRequest, GenerateResponse

router = APIRouter(tags=["generation"])


@router.post(
    "/generate",
    response_model=GenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[RateLimitDep],
)
async def create_generation(
    payload: GenerateRequest,
    partner: PartnerDep,
    service: GenerationServiceDep,
) -> GenerateResponse:
    """Create a task and return immediately (202). The API never waits for
    ComfyUI — poll ``GET /tasks/{id}`` or receive a callback."""
    return await service.create(partner.id, payload)
