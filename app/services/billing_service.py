"""Credit accounting.

The ledger (``billing_entries``) is the source of truth; ``partners.balance_credits``
is a denormalized cache resynced on every mutation. Generation uses a
hold → charge/refund flow so credits are reserved at enqueue and only committed
on success (or released on failure/cancel).
"""

from __future__ import annotations

from app.api.errors import ForbiddenError, InsufficientCreditsError
from app.config.constants import BillingEntryType
from app.database.tables import Partner
from app.repositories.billing import BillingRepository
from app.repositories.partners import PartnerRepository
from app.schemas.billing import BalanceResponse, UsageEntry, UsageResponse


class BillingService:
    def __init__(self, billing_repo: BillingRepository, partner_repo: PartnerRepository) -> None:
        self._billing = billing_repo
        self._partners = partner_repo

    async def balance(self, partner: Partner) -> BalanceResponse:
        bal = await self._billing.balance(partner.id)
        return BalanceResponse(partner_id=partner.id, balance_credits=bal)

    async def usage(self, partner: Partner, *, limit: int = 100, offset: int = 0) -> UsageResponse:
        entries = await self._billing.list_entries(partner.id, limit=limit, offset=offset)
        return UsageResponse(
            partner_id=partner.id,
            entries=[
                UsageEntry(
                    task_id=e.task_id,
                    entry_type=e.entry_type,
                    amount=e.amount,
                    note=e.note,
                    created_at=e.created_at.isoformat(),
                )
                for e in entries
            ],
        )

    async def hold(self, partner: Partner, amount: int, *, task_id: str) -> None:
        """Reserve credits for a task. Raises if the balance is insufficient."""
        if amount <= 0:
            return
        current = await self._billing.balance(partner.id)
        if current < amount:
            raise InsufficientCreditsError(
                details={"required": amount, "balance": current}
            )
        await self._billing.add_entry(
            partner.id, amount=-amount, entry_type=BillingEntryType.HOLD, task_id=task_id, note="generation hold"
        )
        await self._billing.sync_partner_balance(partner)

    async def commit_charge(self, partner_id: int, amount: int, *, task_id: str) -> None:
        """Convert a hold into a permanent charge (net-zero: hold already debited)."""
        if amount <= 0:
            return
        partner = await self._partners.get_by_id(partner_id)
        if partner is None:
            return
        await self._billing.add_entry(
            partner.id, amount=0, entry_type=BillingEntryType.CHARGE, task_id=task_id, note="generation charge"
        )
        await self._billing.sync_partner_balance(partner)

    async def refund(self, partner_id: int, amount: int, *, task_id: str, note: str = "refund") -> None:
        """Release a previously-held amount (task failed/cancelled)."""
        if amount <= 0:
            return
        partner = await self._partners.get_by_id(partner_id)
        if partner is None:
            return
        await self._billing.add_entry(
            partner.id, amount=amount, entry_type=BillingEntryType.REFUND, task_id=task_id, note=note
        )
        await self._billing.sync_partner_balance(partner)

    async def topup(self, partner_id: int, amount: int, *, note: str | None = None) -> BalanceResponse:
        if amount <= 0:
            raise ForbiddenError("Top-up amount must be positive.")
        partner = await self._partners.get_by_id(partner_id)
        if partner is None:
            raise ForbiddenError("Unknown partner.")
        await self._billing.add_entry(
            partner.id, amount=amount, entry_type=BillingEntryType.TOPUP, note=note or "admin top-up"
        )
        bal = await self._billing.sync_partner_balance(partner)
        return BalanceResponse(partner_id=partner.id, balance_credits=bal)
