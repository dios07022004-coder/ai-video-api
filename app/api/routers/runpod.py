"""RunPod Serverless webhook receiver.

RunPod POSTs finished jobs here. The secret in the path authenticates the caller
(RunPod does not sign webhooks), so keep AIV_RUNPOD_WEBHOOK_SECRET private and
serve this only over HTTPS.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import WebhookServiceDep
from app.api.errors import ForbiddenError
from app.config.settings import get_settings

router = APIRouter(tags=["runpod"])


@router.post("/runpod/webhook/{secret}")
async def runpod_webhook(secret: str, request: Request, service: WebhookServiceDep) -> dict[str, Any]:
    settings = get_settings()
    if not hmac.compare_digest(secret, settings.runpod_webhook_secret):
        raise ForbiddenError("Invalid webhook secret.")
    body = await request.json()
    return await service.process(body)
