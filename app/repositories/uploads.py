"""Upload persistence."""

from __future__ import annotations

from sqlalchemy import select

from app.database.tables import Upload
from app.repositories.base import BaseRepository


class UploadRepository(BaseRepository[Upload]):
    model = Upload

    async def get_by_id(self, upload_id: str) -> Upload | None:
        return await self.session.get(Upload, upload_id)

    async def find_by_sha(self, partner_id: int | None, sha256: str) -> Upload | None:
        """Deduplicate identical re-uploads by content hash within a partner."""
        stmt = select(Upload).where(
            Upload.sha256 == sha256, Upload.partner_id == partner_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_url(self, url: str) -> Upload | None:
        stmt = select(Upload).where(Upload.url == url)
        return (await self.session.execute(stmt)).scalar_one_or_none()
