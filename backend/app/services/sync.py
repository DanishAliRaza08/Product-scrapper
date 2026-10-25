"""Own-account synchronisation: catalogue and orders from authorised seller accounts.

Checkpoints are kept per (account, stream). Incremental order sync re-reads a
configurable overlap window so late source updates (refunds, cancellations) are
reconciled; upserts make the overlap idempotent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.connectors.base import AccountCredentials, MarketplaceConnector, SourceType
from app.connectors.registry import get_connector
from app.core.db import session_scope
from app.core.secrets import get_secret_store
from app.models import Job, Listing, MarketplaceAccount, SellerOrder, SellerOrderItem, SyncCheckpoint
from app.services.events import emit
from app.services.ingest import ingest_listings

RECONCILIATION_OVERLAP = timedelta(days=2)
INITIAL_LOOKBACK = timedelta(days=90)


def _checkpoint(db: Session, account_id: uuid.UUID, stream: str) -> SyncCheckpoint:
    cp = db.scalar(
        select(SyncCheckpoint).where(SyncCheckpoint.account_id == account_id, SyncCheckpoint.stream == stream)
    )
    if cp is None:
        cp = SyncCheckpoint(account_id=account_id, stream=stream)
        db.add(cp)
        db.flush()
    return cp


def _load_credentials(account: MarketplaceAccount) -> AccountCredentials:
    if not account.credential_reference:
        raise PermissionError("Account has no stored authorisation")
    return AccountCredentials.model_validate(get_secret_store().get(account.credential_reference))


def _save_credentials(account_ref: str, creds: AccountCredentials) -> None:
    get_secret_store().update(account_ref, creds.model_dump(mode="json"))


async def _sync_catalogue(
    job_id: uuid.UUID, account: MarketplaceAccount, connector: MarketplaceConnector, creds: AccountCredentials
) -> int:
    cursor, total = None, 0
    while True:
        listings, cursor = await connector.list_account_listings(account.marketplace.code, creds, cursor)
        for listing in listings:
            listing.source_type = SourceType.OWN_ACCOUNT
        with session_scope() as db:
            res = ingest_listings(
                db,
                organisation_id=account.organisation_id,
                listings=listings,
                connector_key=connector.manifest.key,
                connector_version=connector.manifest.version,
                job_id=job_id,
                account_id=account.id,
            )
            total += len(res.listing_ids)
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    products_discovered=Job.products_discovered + len(listings),
                    products_processed=Job.products_processed + len(res.listing_ids),
                )
            )
            cp = _checkpoint(db, account.id, "catalogue")
            cp.cursor = cursor
            cp.last_attempt_at = datetime.now(UTC)
            if cursor is None:
                cp.last_successful_sync_at = datetime.now(UTC)
            emit(db, job_id, "catalogue_page", f"Synced {len(listings)} own listings")
        if cursor is None:
            return total


async def _sync_orders(
    job_id: uuid.UUID, account: MarketplaceAccount, connector: MarketplaceConnector, creds: AccountCredentials
) -> int:
    with session_scope() as db:
        cp = _checkpoint(db, account.id, "orders")
        since = (
            (cp.last_successful_sync_at - RECONCILIATION_OVERLAP)
            if cp.last_successful_sync_at
            else (datetime.now(UTC) - INITIAL_LOOKBACK)
        )
        cursor = cp.cursor
    started = datetime.now(UTC)
    total = 0
    while True:
        page = await connector.list_orders(account.marketplace.code, creds, since, cursor)
        with session_scope() as db:
            total += _store_orders(db, account, page.orders)
            cp = _checkpoint(db, account.id, "orders")
            cp.cursor = page.next_cursor
            cp.last_attempt_at = datetime.now(UTC)
            cp.retry_count = 0
            cp.last_error = None
            if page.next_cursor is None:
                cp.last_successful_sync_at = started
            emit(db, job_id, "orders_page", f"Synced {len(page.orders)} orders")
        cursor = page.next_cursor
        if cursor is None:
            return total


def _store_orders(db: Session, account: MarketplaceAccount, orders) -> int:
    if not orders:
        return 0
    stmt = pg_insert(SellerOrder.__table__).values(
        [
            {
                "account_id": account.id,
                "external_order_id": o.external_order_id,
                "order_status": o.order_status,
                "order_currency": o.order_currency,
                "order_total": o.order_total,
                "ordered_at": o.ordered_at,
                "source_updated_at": o.source_updated_at,
            }
            for o in {o.external_order_id: o for o in orders}.values()
        ]
    )
    ex = stmt.excluded
    order_ids = dict(
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["account_id", "external_order_id"],
                set_={
                    c: getattr(ex, c)
                    for c in ("order_status", "order_currency", "order_total", "ordered_at", "source_updated_at")
                },
            ).returning(SellerOrder.__table__.c.external_order_id, SellerOrder.__table__.c.id)
        ).all()
    )
    ext_listing_ids = {i.external_listing_id for o in orders for i in o.items if i.external_listing_id}
    listing_map = dict(
        db.execute(
            select(Listing.external_listing_id, Listing.id).where(
                Listing.organisation_id == account.organisation_id,
                Listing.marketplace_id == account.marketplace_id,
                tuple_(Listing.external_listing_id).in_([(e,) for e in ext_listing_ids] or [("",)]),
            )
        ).all()
    )
    items = [
        {
            "order_id": order_ids[o.external_order_id],
            "external_order_item_id": i.external_order_item_id,
            "listing_id": listing_map.get(i.external_listing_id or ""),
            "seller_sku": i.seller_sku,
            "quantity": i.quantity,
            "unit_price": i.unit_price,
            "item_tax": i.item_tax,
            "marketplace_fees": i.marketplace_fees,
            "refunded_amount": i.refunded_amount,
            "currency_code": i.currency_code,
        }
        for o in orders
        for i in o.items
    ]
    if items:
        stmt = pg_insert(SellerOrderItem.__table__)
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["order_id", "external_order_item_id"],
                set_={
                    c: getattr(stmt.excluded, c)
                    for c in (
                        "listing_id",
                        "seller_sku",
                        "quantity",
                        "unit_price",
                        "item_tax",
                        "marketplace_fees",
                        "refunded_amount",
                        "currency_code",
                    )
                },
            ),
            items,
        )
    return len(order_ids)


async def run_account_sync(job_id: uuid.UUID) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.account_id is not None
        account = db.get(MarketplaceAccount, job.account_id)
        assert account is not None
        streams = job.configuration.get("streams") or ["catalogue", "orders"]
        db.expunge(account)
    connector = get_connector(account.marketplace.platform)
    creds = _load_credentials(account)
    original = creds.model_dump(mode="json")
    try:
        if "catalogue" in streams and "catalogue" in connector.manifest.own_account_streams:
            await _sync_catalogue(job_id, account, connector, creds)
        if "orders" in streams and "orders" in connector.manifest.own_account_streams:
            await _sync_orders(job_id, account, connector, creds)
    finally:
        # Persist rotated/refreshed tokens back to the secrets store, never to SQL.
        if creds.model_dump(mode="json") != original and account.credential_reference:
            _save_credentials(account.credential_reference, creds)
        await connector.aclose()
    with session_scope() as db:
        db.execute(
            update(MarketplaceAccount)
            .where(MarketplaceAccount.id == account.id)
            .values(last_sync_at=datetime.now(UTC), connection_status="connected", last_error=None)
        )
