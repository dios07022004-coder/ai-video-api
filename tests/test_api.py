"""HTTP contract smoke tests using FastAPI's TestClient.

These exercise the endpoints that do not require Redis/ComfyUI/GPU: modes list,
mode preview and the error envelope. The lifespan probes ComfyUI in the
background, so these run without a GPU backend.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def test_list_modes(client: TestClient) -> None:
    resp = client.get("/modes", params={"task_type": "video"})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    ids = {m["id"] for m in body}
    assert "french_kiss" in ids
    # ModeInfo contract fields present
    sample = body[0]
    assert {"id", "name", "category", "enabled", "model", "params"} <= sample.keys()


def test_preview_mode(client: TestClient) -> None:
    resp = client.post("/modes/french_kiss/preview", json={"prompt": "a running horse"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "french_kiss"
    assert "a running horse" in body["prompt"]
    assert "SEED" in body["params"]


def test_unknown_mode_error_envelope(client: TestClient) -> None:
    resp = client.post("/modes/does_not_exist/preview", json={"prompt": "x"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "MODE_NOT_FOUND"


def test_generate_requires_api_key(client: TestClient) -> None:
    resp = client.post(
        "/generate",
        json={
            "task_type": "video",
            "mode": "french_kiss",
            "image_url": "http://x/y.png",
            "user_id": "u1",
            "request_id": "r1",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_admin_requires_token(client: TestClient) -> None:
    resp = client.post("/admin/modes/reload")
    assert resp.status_code == 403


def test_admin_reload_ok(client: TestClient) -> None:
    resp = client.post("/admin/modes/reload", headers={"X-Admin-Token": "test-admin-token"})
    assert resp.status_code == 200
    assert resp.json()["modes"] >= 2
