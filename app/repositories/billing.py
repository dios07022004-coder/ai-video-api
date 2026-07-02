"""Billing ledger persistence (append-only)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from app.config.constants import BillingEntryType
from app.database.tables import BillingEntry, Partner
from app.repositories.base import BaseRepository


class BillingRepository(BaseRepository[BillingEntry]):
    model = BillingEntry

    async def add_entry(
        self,
        partner_id: int,
        *,
        amount: int,
        entry_type: BillingEntryType,
        task_id: str | None = None,
        note: str | None = None,
    ) -> BillingEntry:
        entry = BillingEntry(
            partner_id=partner_id,
            amount=amount,
            entry_type=entry_type.value,
            task_id=task_id,
            note=note,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def balance(self, partner_id: int) -> int:
        """Authoritative balance = sum(ledger). The denormalized column on
        Partner is a cache kept in sync by the service layer."""
        stmt = select(func.coalesce(func.sum(BillingEntry.amount), 0)).where(
            BillingEntry.partner_id == partner_id
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_entries(
        self, partner_id: int, *, limit: int = 100, offset: int = 0
    ) -> Sequence[BillingEntry]:
        stmt = (
            select(BillingEntry)
            .where(BillingEntry.partner_id == partner_id)
            .order_by(BillingEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def sync_partner_balance(self, partner: Partner) -> int:
        partner.balance_credits = await self.balance(partner.id)
        self.session.add(partner)
        return partner.balance_credits
