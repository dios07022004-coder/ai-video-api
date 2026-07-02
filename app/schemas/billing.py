"""Billing schemas (matches OpenAPI `BalanceResponse`/`Usage*`/`TopupRequest`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BalanceResponse(BaseModel):
    partner_id: int
    balance_credits: int


class UsageEntry(BaseModel):
    task_id: str | None = None
    entry_type: str
    amount: int
    note: str | None = None
    created_at: str


class UsageResponse(BaseModel):
    partner_id: int
    entries: list[UsageEntry] = Field(default_factory=list)


class TopupRequest(BaseModel):
    amount: int = Field(..., description="Positive number of credits to grant.")
    note: str | None = Field(default=None, max_length=500)
