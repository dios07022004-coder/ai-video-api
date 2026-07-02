"""Storage backend interface (Dependency Inversion boundary)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredFile:
    key: str          # logical key, e.g. "results/2026/07/02/<uuid>.mp4"
    path: Path        # absolute local path (or cache path)
    url: str          # public absolute URL
    size_bytes: int


class StorageBackend(ABC):
    """Abstract file store. Implementations: local FS, S3, NFS, …"""

    @abstractmethod
    async def save_result(self, data: bytes, *, ext: str, task_id: str) -> StoredFile:
        """Persist a generated artifact under a date-partitioned result key."""

    @abstractmethod
    async def save_upload(self, data: bytes, *, ext: str) -> StoredFile:
        """Persist a validated upload under a date-partitioned upload key."""

    @abstractmethod
    def url_for(self, key: str) -> str:
        """Return the public absolute URL for a stored key."""

    @abstractmethod
    async def cleanup(self, *, older_than_days: int, subdir: str) -> int:
        """Delete artifacts older than N days. Returns number removed."""
