"""Health + GPU/VRAM detection for RunPod.

* ``detect_gpu`` queries ComfyUI's ``/system_stats`` first (authoritative, no
  extra deps), then falls back to ``nvidia-smi`` if available. Never raises.
* ``wait_for_comfy`` blocks at startup until ComfyUI answers (automatic ComfyUI
  detection) or a timeout elapses.
* ``collect_health`` aggregates DB / Redis / ComfyUI / GPU / queue into the
  ``/health`` payload used by RunPod's health check and load balancers.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

from app.comfy.client import make_client
from app.config.settings import get_settings
from app.logging import get_logger

logger = get_logger("runpod.health")


@dataclass
class GpuInfo:
    available: bool = False
    name: str | None = None
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    source: str = "none"


@dataclass
class HealthReport:
    status: str = "ok"
    version: str = "1.0.0"
    components: dict[str, Any] = field(default_factory=dict)
    gpu: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def detect_gpu() -> GpuInfo:
    settings = get_settings()
    # 1. Ask ComfyUI (it already knows the device it loaded on).
    client = make_client(settings.comfy_base_url, settings.comfy_ws_url, suffix="health", timeout=5)
    try:
        stats = await client.system_stats()
        devices = stats.get("devices") or []
        if devices:
            dev = devices[0]
            vram_total = int(dev.get("vram_total", 0)) // (1024 * 1024) or None
            vram_free = int(dev.get("vram_free", 0)) // (1024 * 1024) or None
            return GpuInfo(
                available=True,
                name=dev.get("name"),
                vram_total_mb=vram_total,
                vram_free_mb=vram_free,
                source="comfyui",
            )
    except Exception:  # noqa: BLE001
        pass
    finally:
        await client.aclose()

    # 2. Fall back to nvidia-smi.
    return _nvidia_smi()


def _nvidia_smi() -> GpuInfo:
    if shutil.which("nvidia-smi") is None:
        return GpuInfo(available=False, source="none")
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        first = out.splitlines()[0]
        name, total, free = (p.strip() for p in first.split(","))
        return GpuInfo(
            available=True,
            name=name,
            vram_total_mb=int(float(total)),
            vram_free_mb=int(float(free)),
            source="nvidia-smi",
        )
    except (subprocess.SubprocessError, ValueError, IndexError) as exc:
        logger.warning("nvidia_smi_failed", error=str(exc))
        return GpuInfo(available=False, source="none")


async def wait_for_comfy(*, timeout: float = 120.0, interval: float = 2.0) -> bool:
    """Poll ComfyUI until alive. Returns True if it came up within timeout."""
    settings = get_settings()
    client = make_client(settings.comfy_base_url, settings.comfy_ws_url, suffix="wait", timeout=5)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    try:
        while loop.time() < deadline:
            if await client.is_alive():
                logger.info("comfy_detected", url=settings.comfy_base_url)
                return True
            await asyncio.sleep(interval)
    finally:
        await client.aclose()
    logger.warning("comfy_not_detected", url=settings.comfy_base_url, timeout=timeout)
    return False


async def collect_health() -> HealthReport:
    from app.database import get_database
    from app.queue.broker import get_async_redis
    from app.workflows.registry import get_registry

    report = HealthReport()
    components: dict[str, Any] = {}

    # DB
    components["database"] = "ok" if await get_database().healthcheck() else "down"

    # Redis
    try:
        pong = await get_async_redis().ping()
        components["redis"] = "ok" if pong else "down"
    except Exception:  # noqa: BLE001
        components["redis"] = "down"

    settings = get_settings()
    components["backend"] = settings.generation_backend

    if settings.generation_backend == "runpod":
        # Orchestrator host (e.g. Selectel): check the RunPod endpoint, not a local GPU.
        try:
            from app.runpod.serverless_client import get_runpod_client

            body = await get_runpod_client().health()
            workers = body.get("workers", {})
            components["runpod"] = "ok"
            components["runpod_workers"] = workers
            backend_down = False
        except Exception as exc:  # noqa: BLE001
            components["runpod"] = "down"
            components["runpod_error"] = str(exc)
            backend_down = True
        report.gpu = {"available": False, "source": "remote-runpod"}
    else:
        # In-pod ComfyUI + local GPU.
        client = make_client(settings.comfy_base_url, settings.comfy_ws_url, suffix="health", timeout=5)
        try:
            alive = await client.is_alive()
            components["comfyui"] = "ok" if alive else "down"
            if alive:
                q = await client.queue()
                components["comfy_queue"] = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
            backend_down = not alive
        except Exception:  # noqa: BLE001
            components["comfyui"] = "down"
            backend_down = True
        finally:
            await client.aclose()
        report.gpu = asdict(await detect_gpu())

    # Registry
    snap = get_registry().snapshot
    components["registry"] = {"modes": len(snap.modes), "models": len(snap.models), "errors": len(snap.errors)}

    report.components = components
    critical_down = components.get("database") == "down"
    report.status = "degraded" if (critical_down or backend_down) else "ok"
    return report
