"""Typed application settings, sourced from environment (`AIV_` prefix) / `.env`.

`get_settings()` is process-cached so the whole app shares one immutable config
object. Derived paths are computed once and exposed as helpers so nothing else
in the codebase constructs filesystem paths by hand (No hardcoded paths).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIV_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ──
    env: Literal["dev", "staging", "prod"] = "dev"
    debug: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    public_base_url: str = "http://localhost:8000"

    # ── Security ──
    admin_token: str = "change-me-admin-token-please"
    api_key_header: str = "X-API-Key"
    admin_token_header: str = "X-Admin-Token"
    rate_limit_per_minute: int = 120
    max_upload_mb: int = 25
    allowed_image_types: str = "image/jpeg,image/png,image/webp"
    max_image_pixels: int = 50_000_000
    callback_hmac_secret: str = "change-me-callback-signing-secret"

    # ── Database ──
    database_url: str = "sqlite+aiosqlite:///./data/aivideo.db"

    # ── Redis / queue ──
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "generation"
    job_timeout_seconds: int = 1800
    job_result_ttl_seconds: int = 86_400

    # ── Storage ──
    data_dir: Path = Path("./data")
    uploads_subdir: str = "uploads"
    results_subdir: str = "results"
    result_retention_days: int = 30

    # ── Config (modes/workflows/models) ──
    config_dir: Path = Path("./config")
    modes_subdir: str = "modes"
    workflows_subdir: str = "workflows"
    models_file: str = "models.json"

    # ── Generation backend ──
    # local  → an in-pod RQ worker drives ComfyUI directly (server runs ON RunPod).
    # runpod → this API only orchestrates; generation runs on a RunPod Serverless
    #          endpoint and returns via webhook (server runs on a cheap CPU host).
    generation_backend: Literal["local", "runpod"] = "local"
    runpod_api_key: str = ""
    runpod_endpoint_id: str = ""
    runpod_base_url: str = "https://api.runpod.ai/v2"
    runpod_webhook_secret: str = "change-me-webhook-secret"
    runpod_timeout_seconds: int = 30

    # ── ComfyUI ──
    comfy_host: str = "127.0.0.1"
    comfy_port: int = 8188
    comfy_https: bool = False
    comfy_poll_interval: float = 1.0
    comfy_timeout_seconds: int = 1800
    comfy_input_dir: Path = Path("/workspace/ComfyUI/input")
    comfy_output_dir: Path = Path("/workspace/ComfyUI/output")
    # Comma-separated list of extra endpoints for multi-GPU fan-out (host:port).
    comfy_endpoints: str = ""

    # ── Callbacks ──
    callback_max_retries: int = 5
    callback_timeout_seconds: int = 15

    # ── Logging ──
    log_level: str = "INFO"
    log_json: bool = True
    log_dir: Path = Path("./data/logs")

    # ── Validators / normalizers ─────────────────────────────────────────────
    @field_validator("public_base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def allowed_image_types_set(self) -> frozenset[str]:
        return frozenset(t.strip().lower() for t in self.allowed_image_types.split(",") if t.strip())

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    # ── Derived paths (single source of truth) ───────────────────────────────
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / self.uploads_subdir

    @property
    def results_dir(self) -> Path:
        return self.data_dir / self.results_subdir

    @property
    def modes_dir(self) -> Path:
        return self.config_dir / self.modes_subdir

    @property
    def workflows_dir(self) -> Path:
        return self.config_dir / self.workflows_subdir

    @property
    def models_path(self) -> Path:
        return self.config_dir / self.models_file

    @property
    def control_dir(self) -> Path:
        return self.config_dir / "control"

    @property
    def previews_dir(self) -> Path:
        return self.config_dir / "previews"

    @property
    def comfy_base_url(self) -> str:
        scheme = "https" if self.comfy_https else "http"
        return f"{scheme}://{self.comfy_host}:{self.comfy_port}"

    @property
    def comfy_ws_url(self) -> str:
        scheme = "wss" if self.comfy_https else "ws"
        return f"{scheme}://{self.comfy_host}:{self.comfy_port}/ws"

    @property
    def runpod_run_url(self) -> str:
        return f"{self.runpod_base_url.rstrip('/')}/{self.runpod_endpoint_id}/run"

    @property
    def runpod_status_base(self) -> str:
        return f"{self.runpod_base_url.rstrip('/')}/{self.runpod_endpoint_id}"

    @property
    def runpod_webhook_url(self) -> str:
        """Public URL RunPod calls when a job finishes. Requires public HTTPS."""
        return f"{self.public_base_url}/runpod/webhook/{self.runpod_webhook_secret}"

    @property
    def comfy_endpoint_list(self) -> list[str]:
        primary = f"{self.comfy_host}:{self.comfy_port}"
        extras = [e.strip() for e in self.comfy_endpoints.split(",") if e.strip()]
        return [primary, *extras]

    def ensure_directories(self) -> None:
        """Create all runtime directories. Idempotent; called at startup."""
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.results_dir,
            self.log_dir,
            self.modes_dir,
            self.workflows_dir,
            self.control_dir,
            self.previews_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
