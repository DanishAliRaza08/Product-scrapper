"""Dashboard aggregates. Estimated and actual figures are always reported separately."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Job,
    Listing,
    Marketplace,
    MarketplaceAccount,
    ProductMetric,
    SellerOrder,
    SellerOrderItem,
)

PRICE_BANDS = [0, 10, 25, 50, 100, 250, 500]


def overview(db: Session, organisation_id: uuid.UUID, *, include_own: bool) -> dict:
    base = Listing.organisation_id == organisation_id
    scope = [base] if include_own else [base, Listing.source_type != "own_account"]
    by_marketplace = [
        {
            "marketplace": code,
            "name": name,
            "country": country,
            "listings": n,
            "avg_price": str(round(avg, 2)) if avg is not None else None,
            "currency": currency,
        }
        for code, name, country, currency, n, avg in db.execute(
            select(
                Marketplace.code,
                Marketplace.name,
                Marketplace.country_code,
                Marketplace.currency_code,
                func.count(Listing.id),
                func.avg(Listing.current_price),
            )
            .join(Listing, Listing.marketplace_id == Marketplace.id)
            .where(*scope)
            .group_by(Marketplace.code, Marketplace.name, Marketplace.country_code, Marketplace.currency_code)
            .order_by(func.count(Listing.id).desc())
        )
    ]
    stock = dict(
        db.execute(
            select(func.coalesce(Listing.stock_status, "unknown"), func.count())
            .where(*scope)
            .group_by(Listing.stock_status)
        ).all()
    )
    sources = dict(
        db.execute(select(Listing.source_type, func.count()).where(base).group_by(Listing.source_type)).all()
    )
    if not include_own:
        sources.pop("own_account", None)
    band_expr = case(
        *[(Listing.current_price < hi, f"{lo}-{hi}") for lo, hi in zip(PRICE_BANDS, PRICE_BANDS[1:], strict=False)],
        else_=f"{PRICE_BANDS[-1]}+",
    )
    price_bands = [
        {"band": band, "currency": cur, "listings": n}
        for band, cur, n in db.execute(
            select(band_expr, Listing.currency_code, func.count())
            .where(*scope, Listing.current_price.is_not(None))
            .group_by(band_expr, Listing.currency_code)
        )
    ]
    categories = [
        {"category": c or "Uncategorised", "listings": n}
        for c, n in db.execute(
            select(Listing.category_path, func.count())
            .where(*scope)
            .group_by(Listing.category_path)
            .order_by(func.count().desc())
            .limit(10)
        )
    ]
    day_ago = datetime.now(UTC) - timedelta(days=1)
    changed_24h = db.scalar(select(func.count()).where(*scope, Listing.last_changed_at >= day_ago)) or 0
    stale = (
        db.scalar(select(func.count()).where(*scope, Listing.last_seen_at < datetime.now(UTC) - timedelta(days=7))) or 0
    )
    measurement = dict(
        db.execute(
            select(ProductMetric.measurement_type, func.count(func.distinct(ProductMetric.listing_id)))
            .join(Listing, Listing.id == ProductMetric.listing_id)
            .where(*scope)
            .group_by(ProductMetric.measurement_type)
        ).all()
    )
    jobs = dict(
        db.execute(
            select(Job.job_status, func.count()).where(Job.organisation_id == organisation_id).group_by(Job.job_status)
        ).all()
    )
    return {
        "total_listings": sum(m["listings"] for m in by_marketplace),
        "by_marketplace": by_marketplace,
        "stock_status": stock,
        "source_types": sources,
        "price_bands": price_bands,
        "top_categories": categories,
        "changed_last_24h": changed_24h,
        "stale_over_7_days": stale,
        "listings_with_metrics": measurement,
        "jobs": jobs,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def own_account_summary(db: Session, organisation_id: uuid.UUID, days: int = 30) -> list[dict]:
    """Actual sales from authorised seller accounts only."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(
            MarketplaceAccount.id,
            MarketplaceAccount.account_name,
            Marketplace.code,
            SellerOrderItem.currency_code,
            func.count(func.distinct(SellerOrder.id)),
            func.coalesce(func.sum(SellerOrderItem.quantity), 0),
            func.coalesce(func.sum(SellerOrderItem.unit_price * SellerOrderItem.quantity), 0),
            func.coalesce(func.sum(SellerOrderItem.refunded_amount), 0),
            func.sum(SellerOrderItem.marketplace_fees),
            func.count(SellerOrderItem.id).filter(SellerOrderItem.marketplace_fees.is_(None)),
        )
        .join(Marketplace, Marketplace.id == MarketplaceAccount.marketplace_id)
        .join(SellerOrder, SellerOrder.account_id == MarketplaceAccount.id)
        .join(SellerOrderItem, SellerOrderItem.order_id == SellerOrder.id)
        .where(
            MarketplaceAccount.organisation_id == organisation_id,
            SellerOrder.ordered_at >= since,
            func.coalesce(SellerOrder.order_status, "").notin_(["cancelled", "Canceled", "CANCELLED"]),
        )
        .group_by(
            MarketplaceAccount.id, MarketplaceAccount.account_name, Marketplace.code, SellerOrderItem.currency_code
        )
    ).all()
    out = []
    for acc_id, name, code, currency, orders, units, gross, refunds, fees, items_missing_fees in rows:
        out.append(
            {
                "account_id": str(acc_id),
                "account_name": name,
                "marketplace": code,
                "currency": currency,
                "period_days": days,
                "orders": orders,
                "units": int(units),
                "gross_sales": str(Decimal(gross).quantize(Decimal("0.01"))),
                "refunds": str(Decimal(refunds).quantize(Decimal("0.01"))),
                "marketplace_fees": str(Decimal(fees).quantize(Decimal("0.01"))) if fees is not None else None,
                "fees_complete": items_missing_fees == 0,
                "measurement_type": "actual",
            }
        )
    return out
