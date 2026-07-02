"""Serverless handler tests (ComfyUI mocked — no GPU needed).

Validates input parsing, param resolution, workflow rendering and result
delivery without a real ComfyUI backend.
"""

from __future__ import annotations

import base64

import pytest

import app.serverless.handler as h


class _FakeOutput:
    filename = "aivideo_00001.mp4"
    subfolder = ""
    type = "output"
    kind = "video"


class _FakeResult:
    outputs = [_FakeOutput()]
    primary = _FakeOutput()


class _FakeClient:
    def __init__(self, *a, **k) -> None:
        self.submitted = None

    async def is_alive(self) -> bool:
        return True

    async def upload_image_bytes(self, data: bytes, *, name: str, overwrite: bool = True) -> str:
        return name

    async def upload_image(self, path, *, name=None, overwrite=True) -> str:
        return name or "control.mp4"

    async def submit(self, graph) -> str:
        self.submitted = graph
        return "prompt-123"

    async def history(self, prompt_id):
        return {prompt_id: {"outputs": {"20": {"videos": [{"filename": "aivideo_00001.mp4"}]}}}}

    def parse_result(self, prompt_id, history):
        return _FakeResult()

    async def download(self, output) -> bytes:
        return b"FAKE_MP4_BYTES"

    async def interrupt(self) -> None:  # pragma: no cover
        ...

    async def aclose(self) -> None:
        ...


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(h, "make_client", lambda *a, **k: _FakeClient())
    # Force base64 delivery (no S3 in tests).
    monkeypatch.delenv("AIV_S3_BUCKET", raising=False)


async def test_process_returns_base64_video() -> None:
    img = base64.b64encode(b"not-a-real-image").decode()
    out = await h.process(
        {"mode": "french_kiss", "prompt": "x", "image": img, "params": {"STEPS": 5}, "request_id": "r1"}
    )
    assert out["status"] == "COMPLETED"
    assert out["mode"] == "french_kiss"
    assert out["request_id"] == "r1"
    assert out["delivery"] == "base64"
    assert base64.b64decode(out["video_base64"]) == b"FAKE_MP4_BYTES"


async def test_missing_mode_errors() -> None:
    res = await h.handler({"input": {"prompt": "x"}})
    assert "error" in res
    assert res["error"]["code"] in {"PARAM_INVALID", "MODE_NOT_FOUND"}


async def test_seed_is_injected() -> None:
    out = await h.process({"mode": "french_kiss", "params": {"SEED": 42}})
    assert out["seed"] == 42
