"""ComfyUI endpoint selection for multi-GPU / multi-pod fan-out.

A worker asks the pool for the least-loaded *alive* endpoint. With a single pod
this returns the primary. With N pods (``AIV_COMFY_ENDPOINTS``) it distributes by
live queue depth, enabling horizontal GPU scaling with no code change.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.comfy.client import ComfyClient, make_client
from app.config.settings import Settings, get_settings


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"


class EndpointPool:
    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._endpoints = [self._parse(e) for e in self._s.comfy_endpoint_list]

    @staticmethod
    def _parse(spec: str) -> Endpoint:
        host, _, port = spec.partition(":")
        return Endpoint(host=host, port=int(port or 8188))

    @property
    def endpoints(self) -> list[Endpoint]:
        return list(self._endpoints)

    async def acquire(self, *, worker_suffix: str) -> tuple[Endpoint, ComfyClient]:
        """Return an alive endpoint + client, picking the shortest queue.

        Falls back to the primary endpoint if none report as alive (the client
        call will then surface a typed COMFY_UNAVAILABLE error).
        """
        best: tuple[int, Endpoint] | None = None
        for ep in self._endpoints:
            client = make_client(ep.base_url, ep.ws_url, suffix=worker_suffix)
            try:
                if await client.is_alive():
                    depth = await self._queue_depth(client)
                    if best is None or depth < best[0]:
                        best = (depth, ep)
            finally:
                await client.aclose()

        chosen = best[1] if best else self._endpoints[0]
        client = make_client(
            chosen.base_url, chosen.ws_url, suffix=worker_suffix, timeout=self._s.comfy_timeout_seconds
        )
        return chosen, client

    @staticmethod
    async def _queue_depth(client: ComfyClient) -> int:
        try:
            q = await client.queue()
            return len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
        except Exception:  # noqa: BLE001
            return 10_000
