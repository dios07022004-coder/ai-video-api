"""Admin CLI for provisioning and inspection.

Usage:
    python -m scripts.manage init-db
    python -m scripts.manage create-partner --name "Acme" --email a@b.com --credits 1000
    python -m scripts.manage topup --partner-id 1 --amount 500
    python -m scripts.manage list-modes
    python -m scripts.manage reload

The generated API key is printed **once** — store it securely; only its hash is
persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config.constants import BillingEntryType
from app.config.settings import get_settings
from app.database import get_database, session_scope
from app.database.tables import ApiKey, Partner
from app.logging import configure_logging
from app.repositories.billing import BillingRepository
from app.repositories.partners import PartnerRepository
from app.security.keys import generate_api_key, hash_api_key, key_prefix
from app.workflows.registry import get_registry


async def _init_db() -> None:
    get_settings().ensure_directories()
    await get_database().create_all()
    print("database initialized")


async def _create_partner(name: str, email: str | None, credits: int, label: str | None) -> None:
    plaintext = generate_api_key()
    async with session_scope() as session:
        partner = Partner(name=name, email=email, balance_credits=0)
        session.add(partner)
        await session.flush()

        api_key = ApiKey(
            partner_id=partner.id,
            prefix=key_prefix(plaintext),
            key_hash=hash_api_key(plaintext),
            label=label or "default",
        )
        session.add(api_key)

        if credits > 0:
            billing = BillingRepository(session)
            await billing.add_entry(
                partner.id, amount=credits, entry_type=BillingEntryType.TOPUP, note="initial grant"
            )
            await billing.sync_partner_balance(partner)
        pid = partner.id

    print("partner created")
    print(f"  partner_id : {pid}")
    print(f"  api_key    : {plaintext}")
    print(f"  credits    : {credits}")
    print("  (store the api_key now — it is not recoverable)")


async def _topup(partner_id: int, amount: int) -> None:
    async with session_scope() as session:
        repo = PartnerRepository(session)
        partner = await repo.get_by_id(partner_id)
        if partner is None:
            print(f"partner {partner_id} not found", file=sys.stderr)
            return
        billing = BillingRepository(session)
        await billing.add_entry(partner_id, amount=amount, entry_type=BillingEntryType.TOPUP, note="cli topup")
        bal = await billing.sync_partner_balance(partner)
    print(f"partner {partner_id} balance: {bal}")


def _list_modes() -> None:
    reg = get_registry()
    for mode in reg.list_modes():
        flag = "on " if mode.enabled else "off"
        print(f"  [{flag}] {mode.id:<20} {mode.category:<12} model={mode.model:<12} workflow={mode.workflow}")


def _reload() -> None:
    print(get_registry().reload())


def main() -> int:
    configure_logging(level="INFO", json_output=False, service="cli")
    parser = argparse.ArgumentParser(prog="manage")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db")

    cp = sub.add_parser("create-partner")
    cp.add_argument("--name", required=True)
    cp.add_argument("--email", default=None)
    cp.add_argument("--credits", type=int, default=0)
    cp.add_argument("--label", default=None)

    tp = sub.add_parser("topup")
    tp.add_argument("--partner-id", type=int, required=True)
    tp.add_argument("--amount", type=int, required=True)

    sub.add_parser("list-modes")
    sub.add_parser("reload")

    args = parser.parse_args()
    if args.cmd == "init-db":
        asyncio.run(_init_db())
    elif args.cmd == "create-partner":
        asyncio.run(_create_partner(args.name, args.email, args.credits, args.label))
    elif args.cmd == "topup":
        asyncio.run(_topup(args.partner_id, args.amount))
    elif args.cmd == "list-modes":
        _list_modes()
    elif args.cmd == "reload":
        _reload()
    return 0


if __name__ == "__main__":
    sys.exit(main())
