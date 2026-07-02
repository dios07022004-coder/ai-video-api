"""Async ComfyUI HTTP + WebSocket client.

Speaks the ComfyUI backend protocol:
  POST /prompt            → enqueue a graph, returns prompt_id
  GET  /history/{id}      → execution result + output file descriptors
  GET  /queue             → running/pending queue
  POST /interrupt         → cancel current execution
  GET  /view              → download an output artifact (video/image)
  POST /upload/image      → place a file into ComfyUI's input/ dir
  GET  /system_stats      → health + VRAM/GPU info
  WS   /ws?clientId=...   → live progress + execution events

The client is endpoint-parameterized so a worker can target any GPU pod. All
network faults are surfaced as typed ``AppError``s (never raw httpx errors).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import websockets
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.api.errors import ComfyExecutionError, ComfyUnavailableError
from app.config.constants import COMFY_CLIENT_ID_PREFIX, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from app.logging import get_logger

logger = get_logger("comfy.client")


@dataclass
class ComfyOutput:
    filename: str
    subfolder: str
    type: str  # "output" | "temp"
    node_id: str
    kind: str  # "video" | "image" | "gif"


@dataclass
class ComfyResult:
    prompt_id: str
    outputs: list[ComfyOutput] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def primary(self) -> ComfyOutput | None:
        # Prefer video outputs, else first available.
        videos = [o for o in self.outputs if o.kind in {"video", "gif"}]
        return (videos or self.outputs or [None])[0]


class ComfyClient:
    """One client instance per ComfyUI endpoint."""

    def __init__(
        self,
        base_url: str,
        ws_url: str,
        *,
        client_id: str,
        timeout: float = 60.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._ws = ws_url
        self._client_id = client_id
        self._http = httpx.AsyncClient(base_url=self._base, timeout=timeout)

    @property
    def client_id(self) -> str:
        return self._client_id

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> ComfyClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ── health ───────────────────────────────────────────────────────────────
    async def system_stats(self) -> dict[str, Any]:
        try:
            resp = await self._http.get("/system_stats")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise ComfyUnavailableError(details={"error": str(exc)}) from exc

    async def is_alive(self) -> bool:
        try:
            resp = await self._http.get("/system_stats", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    # ── submit ───────────────────────────────────────────────────────────────
    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=5),
        reraise=True,
    )
    async def submit(self, graph: dict[str, Any]) -> str:
        """Enqueue a rendered graph; return the ComfyUI prompt_id."""
        payload = {"prompt": graph, "client_id": self._client_id}
        try:
            resp = await self._http.post("/prompt", json=payload)
        except httpx.TransportError:
            raise
        except httpx.HTTPError as exc:
            raise ComfyUnavailableError(details={"error": str(exc)}) from exc

        if resp.status_code == 400:
            # Graph validation error from ComfyUI — surface node errors safely.
            detail = _safe_json(resp)
            raise ComfyExecutionError(
                "ComfyUI rejected the workflow graph.",
                details={"node_errors": detail.get("node_errors", detail)},
            )
        if resp.status_code >= 500:
            raise ComfyUnavailableError(details={"status": resp.status_code})

        data = _safe_json(resp)
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyExecutionError("ComfyUI did not return a prompt_id.", details=data)
        logger.info("comfy_submitted", prompt_id=prompt_id, endpoint=self._base)
        return prompt_id

    # ── queue / cancel ───────────────────────────────────────────────────────
    async def queue(self) -> dict[str, Any]:
        resp = await self._http.get("/queue")
        return _safe_json(resp)

    async def interrupt(self) -> None:
        try:
            await self._http.post("/interrupt")
        except httpx.HTTPError as exc:
            logger.warning("comfy_interrupt_failed", error=str(exc))

    # ── history / result ─────────────────────────────────────────────────────
    async def history(self, prompt_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"/history/{prompt_id}")
        return _safe_json(resp)

    def parse_result(self, prompt_id: str, history: dict[str, Any]) -> ComfyResult:
        """Extract output artifacts from a /history entry."""
        entry = history.get(prompt_id) or {}
        status = entry.get("status", {})
        if status.get("status_str") == "error" or status.get("completed") is False and status.get("messages"):
            # look for explicit failure
            for mtype, mdata in status.get("messages", []):
                if mtype == "execution_error":
                    raise ComfyExecutionError(
                        "ComfyUI reported an execution error.",
                        details={"node": mdata.get("node_id"), "message": mdata.get("exception_message")},
                    )
        outputs: list[ComfyOutput] = []
        # Format-agnostic: scan every output key for file descriptors and classify
        # by extension. Handles VHS (gifs/videos), core SaveImage (images) and the
        # newer SaveVideo node regardless of the key it emits.
        for node_id, node_out in (entry.get("outputs") or {}).items():
            if not isinstance(node_out, dict):
                continue
            for key, items in node_out.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict) or "filename" not in item:
                        continue
                    ext = Path(item["filename"]).suffix.lower()
                    if ext in VIDEO_EXTENSIONS:
                        kind = "video"
                    elif ext in IMAGE_EXTENSIONS:
                        kind = "image"
                    elif key in {"gifs", "videos"}:
                        kind = "video"
                    else:
                        kind = "image"
                    outputs.append(
                        ComfyOutput(
                            filename=item["filename"],
                            subfolder=item.get("subfolder", ""),
                            type=item.get("type", "output"),
                            node_id=str(node_id),
                            kind=kind,
                        )
                    )
        return ComfyResult(prompt_id=prompt_id, outputs=outputs, raw=entry)

    # ── file transfer ────────────────────────────────────────────────────────
    async def download(self, output: ComfyOutput) -> bytes:
        params = {"filename": output.filename, "subfolder": output.subfolder, "type": output.type}
        try:
            resp = await self._http.get("/view", params=params)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            raise ComfyExecutionError(
                "Failed to download output artifact from ComfyUI.",
                details={"filename": output.filename, "error": str(exc)},
            ) from exc

    async def upload_image(self, path: Path, *, name: str | None = None, overwrite: bool = True) -> str:
        """Place a local file into ComfyUI's input/ dir; return the name Comfy uses."""
        name = name or path.name
        try:
            with path.open("rb") as fh:
                files = {"image": (name, fh, "application/octet-stream")}
                data = {"overwrite": "true" if overwrite else "false"}
                resp = await self._http.post("/upload/image", files=files, data=data)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyUnavailableError(
                "Failed to upload input image to ComfyUI.", details={"error": str(exc)}
            ) from exc
        body = _safe_json(resp)
        return body.get("name", name)

    async def upload_image_bytes(self, data: bytes, *, name: str, overwrite: bool = True) -> str:
        """Upload raw image bytes into ComfyUI's input/ dir (no temp file needed)."""
        try:
            files = {"image": (name, data, "application/octet-stream")}
            form = {"overwrite": "true" if overwrite else "false"}
            resp = await self._http.post("/upload/image", files=files, data=form)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyUnavailableError(
                "Failed to upload input image bytes to ComfyUI.", details={"error": str(exc)}
            ) from exc
        return _safe_json(resp).get("name", name)

    # ── progress (websocket) ─────────────────────────────────────────────────
    async def watch(self, prompt_id: str, on_event) -> None:  # noqa: ANN001
        """Stream execution events for ``prompt_id`` to ``on_event(dict)``.

        Yields until the prompt finishes (``executing`` with ``node is None`` for
        our prompt) or the socket closes. Progress % is derived from ``progress``
        events. This coroutine is best-effort: history polling is the source of
        truth for completion.
        """
        url = f"{self._ws}?clientId={self._client_id}"
        try:
            async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue  # binary preview frames — ignore
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    mtype = msg.get("type")
                    data = msg.get("data", {})
                    if data.get("prompt_id") not in (None, prompt_id):
                        continue
                    await on_event({"type": mtype, "data": data})
                    if mtype == "executing" and data.get("node") is None and data.get("prompt_id") == prompt_id:
                        return
        except (websockets.WebSocketException, OSError) as exc:
            logger.warning("comfy_ws_closed", error=str(exc), prompt_id=prompt_id)


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return {}


def make_client(base_url: str, ws_url: str, *, suffix: str = "", timeout: float = 60.0) -> ComfyClient:
    client_id = f"{COMFY_CLIENT_ID_PREFIX}-{suffix}" if suffix else COMFY_CLIENT_ID_PREFIX
    return ComfyClient(base_url, ws_url, client_id=client_id, timeout=timeout)
