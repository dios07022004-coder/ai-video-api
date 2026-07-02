"""Storage abstraction. Local filesystem now; swap to S3/NFS without touching callers."""

from app.storage.base import StorageBackend, StoredFile
from app.storage.local import LocalStorage, get_storage

__all__ = ["StorageBackend", "StoredFile", "LocalStorage", "get_storage"]
