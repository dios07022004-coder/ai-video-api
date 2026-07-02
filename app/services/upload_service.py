"""Upload handling: validate → store → register.

The file is stored in our own storage (date-partitioned, UUID name). It is
*not* pushed to ComfyUI here — the worker uploads it to whichever GPU endpoint
runs the job (supports multi-pod). The returned URL is what the website passes
back in ``POST /generate``.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from app.database.tables import Partner, Upload
from app.repositories.uploads import UploadRepository
from app.schemas.uploads import UploadResponse
from app.security.files import ImageValidator
from app.storage.base import StorageBackend


class UploadService:
    def __init__(
        self,
        upload_repo: UploadRepository,
        validator: ImageValidator,
        storage: StorageBackend,
    ) -> None:
        self._repo = upload_repo
        self._validator = validator
        self._storage = storage

    async def upload_image(
        self,
        raw: bytes,
        *,
        declared_type: str | None,
        original_filename: str | None,
        partner: Partner | None,
    ) -> UploadResponse:
        validated = self._validator.validate(raw, declared_type)

        partner_id = partner.id if partner else None
        # Content-addressed dedupe: identical bytes reuse the prior upload.
        existing = await self._repo.find_by_sha(partner_id, validated.sha256)
        if existing is not None:
            return UploadResponse(image_url=existing.url)

        stored = await self._storage.save_upload(validated.data, ext=validated.ext)
        comfy_name = PurePosixPath(stored.key).name  # unique basename for ComfyUI input/

        upload = Upload(
            id=comfy_name.rsplit(".", 1)[0],
            partner_id=partner_id,
            filename=original_filename or comfy_name,
            stored_path=str(stored.path),
            comfy_name=comfy_name,
            url=stored.url,
            content_type=validated.content_type,
            size_bytes=stored.size_bytes,
            width=validated.width,
            height=validated.height,
            sha256=validated.sha256,
        )
        await self._repo.add(upload)
        return UploadResponse(image_url=stored.url)

    async def resolve_local_path(self, image_url: str) -> tuple[str, str] | None:
        """Return (local_path, comfy_name) for a previously uploaded URL, if known."""
        upload = await self._repo.get_by_url(image_url)
        if upload is None:
            return None
        return upload.stored_path, upload.comfy_name
