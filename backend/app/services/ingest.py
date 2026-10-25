"""Bulk persistence of normalised connector records.

* One multi-row ``INSERT ... ON CONFLICT`` per batch (never one transaction per product).
* Descriptive fields keep their last known value when a source omits them; volatile
  fields (price, stock, rank) always reflect the latest successful retrieval.
* Own-account listings are never downgraded to competitor research.
* An observation is written when a listing is new, when a tracked value changed, or
  once per day for unchanged listings, keeping history dense where it matters.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import case, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.connectors.base import MeasurementType, NormalisedListing, SourceType
from app.models import (
    Image,
    Listing,
    Marketplace,
    Observation,
    ProductMetric,
    ProductSource,
    Variation,
)
from app.services.identifiers import normalise_gtin

DESCRIPTIVE = [
    "parent_listing_id",
    "seller_sku",
    "asin",
    "gtin",
    "mpn",
    "description",
    "brand",
    "category_path",
    "category_id",
    "main_image_url",
    "seller_external_id",
    "seller_name",
    "fulfilment_type",
    "condition",
]
VOLATILE = [
    "title",
    "product_url",
    "currency_code",
    "current_price",
    "original_price",
    "shipping_price",
    "stock_status",
    "stock_quantity",
    "stock_quantity_type",
    "rating",
    "review_count",
    "sales_rank",
    "rank_category",
    "attributes",
    "content_hash",
]
PROVENANCE_FIELDS = [
    "title",
    "description",
    "brand",
    "gtin",
    "mpn",
    "category_path",
    "current_price",
    "shipping_price",
    "stock_status",
    "stock_quantity",
    "rating",
    "review_count",
    "sales_rank",
    "seller_name",
    "images",
    "variations",
]


@dataclass
class ListingChange:
    listing_id: int
    external_id: str
    is_new: bool
    old_price: Decimal | None
    new_price: Decimal | None
    old_stock: str | None
    new_stock: str | None
    old_rank: int | None
    new_rank: int | None
    currency: str | None


@dataclass
class IngestResult:
    listing_ids: dict[str, int] = field(default_factory=dict)
    changes: list[ListingChange] = field(default_factory=list)
    inserted: int = 0
    updated: int = 0


def content_hash(n: NormalisedListing) -> str:
    tracked = {
        "price": str(n.current_price),
        "shipping": str(n.shipping_price),
        "stock": n.stock_status.value,
        "qty": n.stock_quantity,
        "rank": n.sales_rank,
        "rating": str(n.rating),
        "reviews": n.review_count,
        "title": n.title,
    }
    return hashlib.sha256(json.dumps(tracked, sort_keys=True).encode()).hexdigest()[:32]


def _row(n: NormalisedListing, organisation_id: uuid.UUID, marketplace_id: int, account_id: uuid.UUID | None) -> dict:
    return {
        "organisation_id": organisation_id,
        "marketplace_id": marketplace_id,
        "account_id": account_id,
        "external_listing_id": n.external_listing_id,
        "parent_listing_id": n.parent_listing_id,
        "seller_sku": n.seller_sku,
        "asin": n.asin,
        "gtin": normalise_gtin(n.gtin),
        "mpn": n.mpn,
        "title": n.title[:1000],
        "description": n.description,
        "brand": n.brand,
        "category_path": n.category_path,
        "category_id": n.category_id,
        "product_url": n.product_url,
        "main_image_url": n.images[0].url if n.images else None,
        "currency_code": n.currency_code,
        "current_price": n.current_price,
        "original_price": n.original_price,
        "shipping_price": n.shipping_price,
        "stock_status": n.stock_status.value,
        "stock_quantity": n.stock_quantity,
        "stock_quantity_type": n.stock_quantity_type.value,
        "condition": n.condition,
        "rating": n.rating,
        "review_count": n.review_count,
        "sales_rank": n.sales_rank,
        "rank_category": n.rank_category,
        "seller_external_id": n.seller_external_id,
        "seller_name": n.seller_name,
        "fulfilment_type": n.fulfilment_type,
        "attributes": n.attributes,
        "source_type": n.source_type.value,
        "content_hash": content_hash(n),
    }


def ingest_listings(
    db: Session,
    *,
    organisation_id: uuid.UUID,
    listings: list[NormalisedListing],
    connector_key: str,
    connector_version: str,
    job_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
) -> IngestResult:
    result = IngestResult()
    if not listings:
        return result
    # De-duplicate within the batch (last one wins) - Postgres rejects an upsert that
    # touches the same row twice.
    by_key: dict[tuple[str, str], NormalisedListing] = {}
    for n in listings:
        by_key[(n.marketplace_code, n.external_listing_id)] = n
    listings = list(by_key.values())

    codes = {n.marketplace_code for n in listings}
    marketplace_ids = dict(
        db.execute(select(Marketplace.code, Marketplace.id).where(Marketplace.code.in_(codes))).all()
    )
    missing = codes - marketplace_ids.keys()
    if missing:
        raise ValueError(f"Unknown marketplace codes: {sorted(missing)}")

    keys = [(marketplace_ids[n.marketplace_code], n.external_listing_id) for n in listings]
    existing = {
        (r.marketplace_id, r.external_listing_id): r
        for r in db.execute(
            select(
                Listing.id,
                Listing.marketplace_id,
                Listing.external_listing_id,
                Listing.content_hash,
                Listing.current_price,
                Listing.stock_status,
                Listing.sales_rank,
                Listing.last_seen_at,
            ).where(
                Listing.organisation_id == organisation_id,
                tuple_(Listing.marketplace_id, Listing.external_listing_id).in_(keys),
            )
        ).all()
    }

    rows = [_row(n, organisation_id, marketplace_ids[n.marketplace_code], account_id) for n in listings]
    table = Listing.__table__
    stmt = pg_insert(table).values(rows)
    ex = stmt.excluded
    set_ = {c: func.coalesce(getattr(ex, c), table.c[c]) for c in DESCRIPTIVE}
    set_.update({c: getattr(ex, c) for c in VOLATILE})
    set_.update(
        {
            "account_id": func.coalesce(table.c.account_id, ex.account_id),
            "source_type": case(
                (table.c.source_type == SourceType.OWN_ACCOUNT.value, table.c.source_type), else_=ex.source_type
            ),
            "last_seen_at": func.now(),
            "updated_at": func.now(),
            "last_changed_at": case(
                (table.c.content_hash.is_distinct_from(ex.content_hash), func.now()), else_=table.c.last_changed_at
            ),
        }
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["organisation_id", "marketplace_id", "external_listing_id"], set_=set_
    ).returning(table.c.id, table.c.marketplace_id, table.c.external_listing_id)
    id_map = {(r.marketplace_id, r.external_listing_id): r.id for r in db.execute(stmt)}

    now = datetime.now(UTC)
    observations, metrics, provenance, variations, images = [], [], [], [], []
    for n in listings:
        key = (marketplace_ids[n.marketplace_code], n.external_listing_id)
        listing_id = id_map[key]
        result.listing_ids[n.external_listing_id] = listing_id
        prev = existing.get(key)
        new_hash = content_hash(n)
        is_new = prev is None
        changed = is_new or prev.content_hash != new_hash
        stale_day = prev is not None and prev.last_seen_at.date() != now.date()
        if is_new:
            result.inserted += 1
        else:
            result.updated += 1
        result.changes.append(
            ListingChange(
                listing_id=listing_id,
                external_id=n.external_listing_id,
                is_new=is_new,
                old_price=prev.current_price if prev else None,
                new_price=n.current_price,
                old_stock=prev.stock_status if prev else None,
                new_stock=n.stock_status.value,
                old_rank=prev.sales_rank if prev else None,
                new_rank=n.sales_rank,
                currency=n.currency_code,
            )
        )
        if changed or stale_day:
            observations.append(
                {
                    "listing_id": listing_id,
                    "observed_at": n.retrieved_at or now,
                    "price": n.current_price,
                    "currency_code": n.currency_code,
                    "shipping_price": n.shipping_price,
                    "stock_status": n.stock_status.value,
                    "stock_quantity": n.stock_quantity,
                    "sales_rank": n.sales_rank,
                    "rank_category": n.rank_category,
                    "rating": n.rating,
                    "review_count": n.review_count,
                    "job_id": job_id,
                    "source_reference": n.source_reference,
                }
            )
            for m in n.metrics:
                metrics.append(
                    {
                        "listing_id": listing_id,
                        "metric_name": m.name,
                        "numeric_value": None if m.measurement_type == MeasurementType.UNAVAILABLE else m.value,
                        "unit": m.unit,
                        "measurement_type": m.measurement_type.value,
                        "period_start": m.period_start,
                        "period_end": m.period_end,
                        "methodology": m.methodology,
                        "confidence": m.confidence,
                        "source": m.source,
                        "observed_at": n.retrieved_at or now,
                    }
                )
        for f in PROVENANCE_FIELDS:
            value = getattr(n, f, None)
            if value in (None, [], ""):
                continue
            provenance.append(
                {
                    "listing_id": listing_id,
                    "field_name": f,
                    "source_type": n.source_type.value,
                    "connector": connector_key,
                    "connector_version": connector_version,
                    "endpoint": n.endpoint,
                    "source_reference": n.source_reference,
                    "retrieved_at": n.retrieved_at or now,
                }
            )
        for v in n.variations:
            variations.append(
                {
                    "listing_id": listing_id,
                    "external_variation_id": v.external_variation_id,
                    "variation_sku": v.variation_sku,
                    "size": v.size,
                    "colour": v.colour,
                    "price": v.price,
                    "currency_code": v.currency_code,
                    "stock_status": v.stock_status.value if v.stock_status else None,
                    "attributes": v.attributes,
                }
            )
        for order, img in enumerate(n.images):
            images.append(
                {
                    "listing_id": listing_id,
                    "image_url": img.url,
                    "image_type": img.image_type,
                    "width": img.width,
                    "height": img.height,
                    "display_order": order,
                }
            )

    if observations:
        db.execute(pg_insert(Observation.__table__), observations)
    if metrics:
        db.execute(pg_insert(ProductMetric.__table__), metrics)
    if provenance:
        stmt = pg_insert(ProductSource.__table__)
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["listing_id", "field_name"],
                set_={
                    c: getattr(stmt.excluded, c)
                    for c in (
                        "source_type",
                        "connector",
                        "connector_version",
                        "endpoint",
                        "source_reference",
                        "retrieved_at",
                    )
                },
            ),
            provenance,
        )
    if variations:
        stmt = pg_insert(Variation.__table__)
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["listing_id", "external_variation_id"],
                index_where=text("external_variation_id IS NOT NULL"),
                set_={
                    **{
                        c: getattr(stmt.excluded, c)
                        for c in (
                            "variation_sku",
                            "size",
                            "colour",
                            "price",
                            "currency_code",
                            "stock_status",
                            "attributes",
                        )
                    },
                    "updated_at": func.now(),
                },
            ),
            variations,
        )
    if images:
        stmt = pg_insert(Image.__table__)
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["listing_id", "image_url"],
                set_={"display_order": stmt.excluded.display_order, "image_type": stmt.excluded.image_type},
            ),
            images,
        )
    return result
