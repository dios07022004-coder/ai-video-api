"""RunPod backend: URL building, guard rails, webhook matching shape."""

from __future__ import annotations

import pytest

from app.api.errors import ConflictError
from app.config.settings import Settings
from app.runpod.serverless_client import RunPodClient


def test_runpod_urls_built_from_settings() -> None:
    s = Settings(
        runpod_base_url="https://api.runpod.ai/v2",
        runpod_endpoint_id="abc123",
        runpod_webhook_secret="s3cr3t",
        public_base_url="https://api.example.com/",
    )
    assert s.runpod_run_url == "https://api.runpod.ai/v2/abc123/run"
    assert s.runpod_status_base == "https://api.runpod.ai/v2/abc123"
    assert s.runpod_webhook_url == "https://api.example.com/runpod/webhook/s3cr3t"


def test_client_requires_credentials() -> None:
    with pytest.raises(ConflictError):
        RunPodClient(api_key="", endpoint_id="", base_url="https://x", timeout=5)


def test_client_ok_with_credentials() -> None:
    c = RunPodClient(api_key="k", endpoint_id="e", base_url="https://api.runpod.ai/v2", timeout=5)
    assert c._base == "https://api.runpod.ai/v2/e"  # noqa: SLF001 — white-box check
