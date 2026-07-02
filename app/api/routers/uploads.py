"""POST /uploads — validate and store an input image."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.api.deps import OptionalPartnerDep, RateLimitDep, UploadServiceDep
from app.schemas.uploads import UploadResponse

router = APIRouter(tags=["uploads"])


@router.post("/uploads", response_model=UploadResponse, dependencies=[RateLimitDep])
async def upload_image(
    service: UploadServiceDep,
    partner: OptionalPartnerDep,
    file: UploadFile = File(...),
) -> UploadResponse:
    """Accept a multipart image, validate + re-encode it, return its URL.

    The bytes are read fully so streaming size limits are enforced by the
    validator (large files are rejected before persistence).
    """
    raw = await file.read()
    return await service.upload_image(
        raw,
        declared_type=file.content_type,
        original_filename=file.filename,
        partner=partner,
    )
