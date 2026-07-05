"""Soft apparent-age gate for user uploads.

A best-effort safety filter for an NSFW platform: estimate the apparent age of
the youngest face in an uploaded photo and block images that look clearly under
the configured floor. This is **not** legal age verification (that needs an ID
document) — face-based age estimation carries a ±5–10 year error, so the check
is deliberately lenient: it only rejects faces well below the threshold and,
by default, allows photos with no detectable face (fail-open).

Backed by InsightFace (SCRFD detector + genderage model, ONNX, CPU-capable).
The heavy model is loaded once, lazily, and inference runs in a worker thread so
it never blocks the event loop. If InsightFace/onnxruntime isn't installed or a
model can't load, the estimator degrades to "unavailable" and the caller decides
(fail-open by default).
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from app.config.settings import Settings, get_settings
from app.logging import get_logger

logger = get_logger("security.age")


@dataclass(frozen=True)
class AgeEstimate:
    available: bool          # was the model able to run at all?
    faces: int               # number of faces detected
    min_age: float | None    # apparent age of the youngest detected face


class AgeEstimator:
    """Lazily-loaded, thread-safe wrapper around an InsightFace analyser."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._app = None            # insightface.app.FaceAnalysis | None
        self._load_failed = False
        self._lock = asyncio.Lock()

    def _load_sync(self) -> object | None:
        try:
            from insightface.app import FaceAnalysis  # type: ignore
        except Exception as exc:  # noqa: BLE001 — lib not installed / import error
            logger.warning("age_model_import_failed", error=repr(exc))
            return None
        try:
            app = FaceAnalysis(
                name=self._s.age_model_name,
                allowed_modules=["detection", "genderage"],
                providers=["CPUExecutionProvider"],
            )
            size = self._s.age_det_size
            app.prepare(ctx_id=-1, det_size=(size, size))  # ctx_id=-1 → CPU
            logger.info("age_model_loaded", model=self._s.age_model_name, det_size=size)
            return app
        except Exception as exc:  # noqa: BLE001 — model files missing / runtime error
            logger.warning("age_model_load_failed", error=repr(exc))
            return None

    async def _ensure_loaded(self) -> object | None:
        if self._app is not None or self._load_failed:
            return self._app
        async with self._lock:
            if self._app is None and not self._load_failed:
                app = await asyncio.to_thread(self._load_sync)
                if app is None:
                    self._load_failed = True
                self._app = app
        return self._app

    def _analyze_sync(self, app: object, data: bytes) -> AgeEstimate:
        try:
            import numpy as np  # type: ignore
            from PIL import Image
        except Exception as exc:  # noqa: BLE001
            logger.warning("age_deps_missing", error=repr(exc))
            return AgeEstimate(available=False, faces=0, min_age=None)
        try:
            with Image.open(io.BytesIO(data)) as im:
                rgb = im.convert("RGB")
                arr = np.asarray(rgb)[:, :, ::-1]  # RGB → BGR for InsightFace
            faces = app.get(arr)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("age_inference_failed", error=repr(exc))
            return AgeEstimate(available=False, faces=0, min_age=None)

        ages = [float(f.age) for f in faces if getattr(f, "age", None) is not None]
        min_age = min(ages) if ages else None
        return AgeEstimate(available=True, faces=len(faces), min_age=min_age)

    async def estimate(self, data: bytes) -> AgeEstimate:
        app = await self._ensure_loaded()
        if app is None:
            return AgeEstimate(available=False, faces=0, min_age=None)
        return await asyncio.to_thread(self._analyze_sync, app, data)


_estimator: AgeEstimator | None = None


def get_age_estimator() -> AgeEstimator:
    global _estimator
    if _estimator is None:
        _estimator = AgeEstimator()
    return _estimator
