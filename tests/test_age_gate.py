"""Soft age-gate logic tests (no InsightFace / model needed — estimator faked).

Exercises UploadService._check_age against the configured thresholds: reject
clearly-underage faces, allow adults, and honour the no-face / fail-open knobs.
"""

from __future__ import annotations

import pytest

from app.api.errors import AgeVerificationError
from app.config.settings import Settings
from app.security.age import AgeEstimate
from app.services.upload_service import UploadService


class _FakeEstimator:
    def __init__(self, est: AgeEstimate) -> None:
        self._est = est

    async def estimate(self, data: bytes) -> AgeEstimate:
        return self._est


def _service(est: AgeEstimate, **overrides) -> UploadService:
    settings = Settings(age_check_enabled=True, **overrides)
    return UploadService(None, None, None, settings, _FakeEstimator(est))  # type: ignore[arg-type]


async def test_rejects_clearly_underage() -> None:
    svc = _service(AgeEstimate(available=True, faces=1, min_age=14.0))
    with pytest.raises(AgeVerificationError):
        await svc._check_age(b"img")


async def test_allows_adult() -> None:
    svc = _service(AgeEstimate(available=True, faces=1, min_age=27.0))
    await svc._check_age(b"img")  # no raise


async def test_youngest_face_decides() -> None:
    # min_age reflects the youngest of several faces.
    svc = _service(AgeEstimate(available=True, faces=3, min_age=15.0))
    with pytest.raises(AgeVerificationError):
        await svc._check_age(b"img")


async def test_no_face_allowed_by_default() -> None:
    svc = _service(AgeEstimate(available=True, faces=0, min_age=None))
    await svc._check_age(b"img")  # soft: allow when we can't see a face


async def test_no_face_blocked_when_required() -> None:
    svc = _service(AgeEstimate(available=True, faces=0, min_age=None), age_require_face=True)
    with pytest.raises(AgeVerificationError):
        await svc._check_age(b"img")


async def test_fail_open_when_model_unavailable() -> None:
    svc = _service(AgeEstimate(available=False, faces=0, min_age=None))
    await svc._check_age(b"img")  # fail-open default → allow


async def test_fail_closed_when_configured() -> None:
    svc = _service(AgeEstimate(available=False, faces=0, min_age=None), age_fail_open=False)
    with pytest.raises(AgeVerificationError):
        await svc._check_age(b"img")


async def test_disabled_skips_check() -> None:
    settings = Settings(age_check_enabled=False)
    # Estimator would reject, but the gate is off → allowed.
    svc = UploadService(None, None, None, settings, _FakeEstimator(  # type: ignore[arg-type]
        AgeEstimate(available=True, faces=1, min_age=10.0)))
    await svc._check_age(b"img")
