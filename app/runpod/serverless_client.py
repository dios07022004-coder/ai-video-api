"""Client for a RunPod Serverless endpoint (the GPU backend).

Used when ``AIV_GENERATION_BACKEND=runpod``: this API dispatches jobs to RunPod
via `/run` (async, returns a job id immediately) and receives results through a
webhook. `/status` and `/cancel` are provided for reconciliation of stuck jobs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx

from app.api.errors import ComfyExecutionError, ComfyUnavailableError, ConflictError
from app.config.settings import Settings, get_settings
from app.logging import get_logger

logger = get_logger("runpod.client")


class RunPodClient:
    def __init__(self, *, api_key: str, endpoint_id: str, base_url: str, timeout: float) -> None:
        if not api_key or not endpoint_id:
            raise ConflictError(
                "RunPod backend is selected but RUNPOD_API_KEY / RUNPOD_ENDPOINT_ID are not set."
            )
        self._base = f"{base_url.rstrip('/')}/{endpoint_id}"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout

    async def run(self, job_input: dict[str, Any], *, webhook: str | None = None) -> str:
        """Enqueue a job. Returns the RunPod job id (non-blocking)."""
        payload: dict[str, Any] = {"input": job_input}
        if webhook:
            payload["webhook"] = webhook
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/run", json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ComfyUnavailableError(
                "Could not reach RunPod endpoint.", details={"error": str(exc)}
            ) from exc

        if resp.status_code >= 500:
            raise ComfyUnavailableError(details={"status": resp.status_code})
        if resp.status_code >= 400:
            raise ComfyExecutionError(
                "RunPod rejected the job.", details={"status": resp.status_code, "body": _safe(resp)}
            )
        data = _safe(resp)
        job_id = data.get("id")
        if not job_id:
            raise ComfyExecutionError("RunPod did not return a job id.", details=data)
        logger.info("runpod_dispatched", job_id=job_id, status=data.get("status"))
        return job_id

    async def status(self, job_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base}/status/{job_id}", headers=self._headers)
        return _safe(resp)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base}/cancel/{job_id}", headers=self._headers)
        return _safe(resp)

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base}/health", headers=self._headers)
        return _safe(resp)


def _safe(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except ValueError:
        return {}


@lru_cache(maxsize=1)
def get_runpod_client(settings: Settings | None = None) -> RunPodClient:
    s = settings or get_settings()
    return RunPodClient(
        api_key=s.runpod_api_key,
        endpoint_id=s.runpod_endpoint_id,
        base_url=s.runpod_base_url,
        timeout=s.runpod_timeout_seconds,
    )
