"""Local filesystem storage backend.

Artifacts are written under date-partitioned directories with UUID filenames:
    results/YYYY/MM/DD/<uuid>.mp4
    uploads/YYYY/MM/DD/<uuid>.png
Public URLs are served by NGINX (or FastAPI StaticFiles in dev) from the data dir.
Writes are atomic (temp file + rename) so a reader never sees a partial file.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import aiofiles

from app.api.errors import StorageError
from app.config.settings import Settings, get_settings
from app.logging import get_logger
from app.storage.base import StorageBackend, StoredFile

logger = get_logger("storage.local")


class LocalStorage(StorageBackend):
    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()

    # ── path helpers ─────────────────────────────────────────────────────────
    def _date_parts(self) -> tuple[str, str, str]:
        now = datetime.now(UTC)
        return f"{now:%Y}", f"{now:%m}", f"{now:%d}"

    def _result_key(self, ext: str) -> str:
        y, m, d = self._date_parts()
        return f"{self._s.results_subdir}/{y}/{m}/{d}/{uuid.uuid4().hex}{_dot(ext)}"

    def _upload_key(self, ext: str) -> str:
        y, m, d = self._date_parts()
        return f"{self._s.uploads_subdir}/{y}/{m}/{d}/{uuid.uuid4().hex}{_dot(ext)}"

    def _abs(self, key: str) -> Path:
        return (self._s.data_dir / key).resolve()

    # ── writes ───────────────────────────────────────────────────────────────
    async def _write_atomic(self, key: str, data: bytes) -> StoredFile:
        abspath = self._abs(key)
        try:
            abspath.parent.mkdir(parents=True, exist_ok=True)
            tmp = abspath.with_suffix(abspath.suffix + ".part")
            async with aiofiles.open(tmp, "wb") as fh:
                await fh.write(data)
            tmp.replace(abspath)
        except OSError as exc:
            raise StorageError("Failed to write artifact.", details={"key": key, "error": str(exc)}) from exc
        stored = StoredFile(key=key, path=abspath, url=self.url_for(key), size_bytes=len(data))
        logger.info("stored", key=key, bytes=len(data))
        return stored

    async def save_result(self, data: bytes, *, ext: str, task_id: str) -> StoredFile:
        return await self._write_atomic(self._result_key(ext), data)

    async def save_upload(self, data: bytes, *, ext: str) -> StoredFile:
        return await self._write_atomic(self._upload_key(ext), data)

    def url_for(self, key: str) -> str:
        return f"{self._s.public_base_url}/files/{key}"

    # ── cleanup ──────────────────────────────────────────────────────────────
    async def cleanup(self, *, older_than_days: int, subdir: str) -> int:
        root = self._s.data_dir / subdir
        if not root.exists():
            return 0
        cutoff = datetime.now(UTC).timestamp() - older_than_days * 86_400
        removed = 0
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    logger.warning("cleanup_unlink_failed", path=str(path))
        logger.info("cleanup_done", subdir=subdir, removed=removed, older_than_days=older_than_days)
        return removed


def _dot(ext: str) -> str:
    ext = ext.lstrip(".")
    return f".{ext}" if ext else ""


@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    """Storage singleton. Swap the return type here to change backends globally."""
    return LocalStorage()
