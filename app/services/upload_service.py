"""Upload handling: validate → store → register.

The file is stored in our own storage (date-partitioned, UUID name). It is
*not* pushed to ComfyUI here — the worker uploads it to whichever GPU endpoint
runs the job (supports multi-pod). The returned URL is what the website passes
back in ``POST /generate``.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from app.api.errors import AgeVerificationError
from app.config.settings import Settings, get_settings
from app.database.tables import Partner, Upload
from app.logging import get_logger
from app.repositories.uploads import UploadRepository
from app.schemas.uploads import UploadResponse
from app.security.age import AgeEstimator, get_age_estimator
from app.security.files import ImageValidator
from app.storage.base import StorageBackend

logger = get_logger("services.upload")


class UploadService:
    def __init__(
        self,
        upload_repo: UploadRepository,
        validator: ImageValidator,
        storage: StorageBackend,
        settings: Settings | None = None,
        age_estimator: AgeEstimator | None = None,
    ) -> None:
        self._repo = upload_repo
        self._validator = validator
        self._storage = storage
        self._s = settings or get_settings()
        self._age = age_estimator or get_age_estimator()

    async def upload_image(
        self,
        raw: bytes,
        *,
        declared_type: str | None,
        original_filename: str | None,
        partner: Partner | None,
    ) -> UploadResponse:
        validated = self._validator.validate(raw, declared_type)

        # Soft age gate on the clean, re-encoded bytes (before we store anything).
        await self._check_age(validated.data)

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

    async def _check_age(self, data: bytes) -> None:
        """Best-effort apparent-age gate. Raises AgeVerificationError to block."""
        if not self._s.age_check_enabled:
            return

        est = await self._age.estimate(data)

        # Model couldn't run (not installed / load error): honour fail-open.
        if not est.available:
            if self._s.age_fail_open:
                logger.warning("age_check_unavailable_allow")
                return
            raise AgeVerificationError(
                "Age verification is temporarily unavailable. Please try again later."
            )

        # No face found — can't estimate. Allow unless configured to require one.
        if est.faces == 0:
            if self._s.age_require_face:
                raise AgeVerificationError(
                    "No face could be detected to verify age. Please upload a clear "
                    "photo showing the face.",
                    details={"faces": 0},
                )
            logger.info("age_check_no_face_allow")
            return

        min_age = est.min_age
        if min_age is not None and min_age < self._s.age_reject_below:
            logger.info(
                "age_check_rejected", apparent_age=round(min_age, 1), faces=est.faces,
                reject_below=self._s.age_reject_below,
            )
            raise AgeVerificationError(
                f"The person in the image appears to be under {self._s.age_min_years}. "
                "Uploads must depict adults only.",
                details={
                    "apparent_age": round(min_age, 1),
                    "min_required": self._s.age_min_years,
                },
            )
        logger.info("age_check_passed", apparent_age=round(min_age, 1) if min_age else None, faces=est.faces)

    async def resolve_local_path(self, image_url: str) -> tuple[str, str] | None:
        """Return (local_path, comfy_name) for a previously uploaded URL, if known."""
        upload = await self._repo.get_by_url(image_url)
        if upload is None:
            return None
        return upload.stored_path, upload.comfy_name
