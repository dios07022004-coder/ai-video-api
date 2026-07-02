"""Partner + API key persistence."""

from __future__ import annotations

from sqlalchemy import select

from app.database.base import utcnow
from app.database.tables import ApiKey, Partner
from app.repositories.base import BaseRepository


class PartnerRepository(BaseRepository[Partner]):
    model = Partner

    async def get_by_id(self, partner_id: int) -> Partner | None:
        return await self.session.get(Partner, partner_id)

    async def get_by_api_key_hash(self, key_hash: str) -> tuple[Partner, ApiKey] | None:
        """Resolve an active partner + key from the hashed API key."""
        stmt = (
            select(Partner, ApiKey)
            .join(ApiKey, ApiKey.partner_id == Partner.id)
            .where(
                ApiKey.key_hash == key_hash,
                ApiKey.is_active.is_(True),
                Partner.is_active.is_(True),
            )
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        return row[0], row[1]

    async def touch_key(self, api_key: ApiKey) -> None:
        api_key.last_used_at = utcnow()
        self.session.add(api_key)

    async def add_key(self, api_key: ApiKey) -> ApiKey:
        self.session.add(api_key)
        await self.session.flush()
        return api_key
