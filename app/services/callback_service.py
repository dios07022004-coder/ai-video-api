"""Partner callback delivery with retries + HMAC signing.

On terminal task states we POST a signed JSON body to the partner's
``callback_url``. The signature (``X-Signature: sha256=<hex>``) lets the partner
verify authenticity. Delivery retries with exponential backoff; permanent
failure is logged but never fails the generation itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import httpx

from app.config.settings import get_settings
from app.logging import get_logger

logger = get_logger("services.callback")


class CallbackService:
    def __init__(self) -> None:
        self._s = get_settings()

    def _sign(self, body: bytes) -> str:
        digest = hmac.new(
            self._s.callback_hmac_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return f"sha256={digest}"

    async def fire(
        self,
        callback_url: str | None,
        *,
        task_id: str,
        status: str,
        result_url: str | None,
        duration_ms: int | None,
        credits: int,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not callback_url:
            return False

        payload = {
            "task_id": task_id,
            "status": status,
            "result_url": result_url,
            "duration": (duration_ms / 1000.0) if duration_ms is not None else None,
            "credits": credits,
            "metadata": metadata or {},
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Signature": self._sign(body),
            "X-Task-Id": task_id,
        }

        max_retries = max(1, self._s.callback_max_retries)
        for attempt in range(1, max_retries + 1):
            ok = await self._post_once(callback_url, body, headers)
            if ok:
                logger.info("callback_delivered", task_id=task_id, url=callback_url, attempt=attempt)
                return True
            if attempt < max_retries:
                backoff = min(30.0, 2.0 ** (attempt - 1))  # 1s, 2s, 4s, 8s, … capped 30s
                await asyncio.sleep(backoff)
        logger.warning("callback_failed", task_id=task_id, url=callback_url, attempts=max_retries)
        return False

    async def _post_once(self, url: str, body: bytes, headers: dict[str, str]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._s.callback_timeout_seconds) as client:
                resp = await client.post(url, content=body, headers=headers)
            return 200 <= resp.status_code < 300
        except httpx.HTTPError as exc:
            logger.info("callback_attempt_error", url=url, error=str(exc))
            return False
