"""Billing: partner balance/usage + admin top-up/view."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import (
    AdminDep,
    BillingServiceDep,
    PartnerDep,
)
from app.schemas.billing import BalanceResponse, TopupRequest, UsageResponse

router = APIRouter(tags=["billing"])


@router.get("/billing/balance", response_model=BalanceResponse)
async def get_balance(partner: PartnerDep, service: BillingServiceDep) -> BalanceResponse:
    return await service.balance(partner)


@router.get("/billing/usage", response_model=UsageResponse)
async def get_usage(
    partner: PartnerDep,
    service: BillingServiceDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> UsageResponse:
    return await service.usage(partner, limit=limit, offset=offset)


@router.post("/admin/billing/{partner_id}/topup", response_model=BalanceResponse, tags=["billing"])
async def admin_topup(
    partner_id: int,
    payload: TopupRequest,
    _admin: AdminDep,
    service: BillingServiceDep,
) -> BalanceResponse:
    return await service.topup(partner_id, payload.amount, note=payload.note)


@router.get("/admin/billing/{partner_id}", response_model=UsageResponse, tags=["billing"])
async def admin_view(
    partner_id: int,
    _admin: AdminDep,
    service: BillingServiceDep,
    limit: int = Query(default=200, ge=1, le=1000),
) -> UsageResponse:
    # Reuse usage listing for the given partner id (admin-scoped).
    from app.database.tables import Partner

    stub = Partner(id=partner_id, name="")
    return await service.usage(stub, limit=limit)
