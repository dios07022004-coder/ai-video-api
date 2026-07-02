"""Result delivery for serverless jobs.

Two modes, chosen by env:
  * S3/R2 upload (recommended for video): set AIV_S3_BUCKET (+ creds/endpoint).
    Returns a URL your server stores directly — small job responses, no base64.
  * Base64 fallback: no S3 configured → the video is returned inline. Fine for
    short clips, but RunPod caps job output size, so prefer S3 for real video.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime

from app.logging import get_logger

logger = get_logger("serverless.storage")


def _s3_enabled() -> bool:
    return bool(os.getenv("AIV_S3_BUCKET"))


def deliver(data: bytes, *, ext: str, content_type: str) -> dict:
    """Return a delivery descriptor: either {video_url} or {video_base64}."""
    if _s3_enabled():
        try:
            url = _upload_s3(data, ext=ext, content_type=content_type)
            return {"delivery": "url", "video_url": url, "format": ext}
        except Exception as exc:  # noqa: BLE001 — never lose a result to a storage hiccup
            logger.warning("s3_upload_failed_fallback_base64", error=str(exc))

    return {
        "delivery": "base64",
        "format": ext,
        "content_type": content_type,
        "video_base64": base64.b64encode(data).decode("ascii"),
    }


def _upload_s3(data: bytes, *, ext: str, content_type: str) -> str:
    """Upload to any S3-compatible store (AWS S3, Cloudflare R2, Backblaze B2)."""
    import boto3  # lazy — only needed when S3 is configured

    bucket = os.environ["AIV_S3_BUCKET"]
    endpoint = os.getenv("AIV_S3_ENDPOINT")  # e.g. https://<acct>.r2.cloudflarestorage.com
    region = os.getenv("AIV_S3_REGION", "auto")
    public_base = os.getenv("AIV_S3_PUBLIC_BASE")  # CDN/public bucket URL, optional

    now = datetime.now(UTC)
    key = f"results/{now:%Y/%m/%d}/{uuid.uuid4().hex}.{ext}"

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=os.getenv("AIV_S3_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("AIV_S3_SECRET_KEY"),
    )
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)

    if public_base:
        return f"{public_base.rstrip('/')}/{key}"
    # Presigned URL (valid 7 days) if the bucket isn't public.
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=7 * 24 * 3600
    )
