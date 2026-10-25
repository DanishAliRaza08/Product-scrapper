"""Per-listing profit and opportunity analysis from stored data."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    FeeProfile,
    Image,
    Listing,
    Marketplace,
    Observation,
    Organisation,
    ProductCost,
    ProductMatch,
    ProductMetric,
)
from app.services import fx, profit, scoring


def latest_cost(
    db: Session, organisation_id: uuid.UUID, listing: Listing, at: datetime | None = None
) -> ProductCost | None:
    at = at or datetime.now(UTC)
    conditions = [ProductCost.listing_id == listing.id]
    if listing.canonical_product_id:
        conditions.append(ProductCost.canonical_product_id == listing.canonical_product_id)
    return db.scalar(
        select(ProductCost)
        .where(ProductCost.organisation_id == organisation_id, or_(*conditions), ProductCost.effective_from <= at)
        .order_by(ProductCost.listing_id.is_(None), ProductCost.effective_from.desc())  # listing-specific first
        .limit(1)
    )


def resolve_fee_profile(
    db: Session, organisation_id: uuid.UUID, listing: Listing, cost: ProductCost | None
) -> FeeProfile | None:
    if cost is not None and cost.fee_profile_id:
        return db.get(FeeProfile, cost.fee_profile_id)
    candidates = db.scalars(
        select(FeeProfile)
        .where(
            FeeProfile.marketplace_id == listing.marketplace_id,
            or_(FeeProfile.organisation_id == organisation_id, FeeProfile.organisation_id.is_(None)),
            FeeProfile.effective_from <= func.current_date(),
        )
        .order_by(FeeProfile.organisation_id.is_(None), FeeProfile.effective_from.desc())
    ).all()
    category = (listing.category_path or "").lower()
    specific = [c for c in candidates if c.category_pattern and c.category_pattern.lower() in category]
    generic = [c for c in candidates if not c.category_pattern]
    return (specific or generic or [None])[0]


def listing_profit(
    db: Session,
    organisation_id: uuid.UUID,
    listing: Listing,
    *,
    selling_price: Decimal | None = None,
    target_margin_pct: Decimal | None = None,
) -> profit.ProfitResult:
    marketplace = db.get(Marketplace, listing.marketplace_id)
    assert marketplace is not None
    cost = latest_cost(db, organisation_id, listing)
    fee_profile = resolve_fee_profile(db, organisation_id, listing, cost)
    currency = listing.currency_code or marketplace.currency_code
    fx_used: list[dict] = []

    def to_selling(amount: Decimal | None, from_currency: str) -> Decimal | None:
        if amount is None or from_currency == currency:
            return amount
        rate = fx.find_rate(db, from_currency, currency)
        if rate is None:
            return None
        fx_used.append(
            {
                "from": from_currency,
                "to": currency,
                "rate": str(rate.rate),
                "source": rate.source,
                "effective_at": rate.effective_at.isoformat(),
            }
        )
        return amount * rate.rate

    missing_fx = False
    inputs = profit.CostInputs(
        selling_price=selling_price if selling_price is not None else listing.current_price,
        currency_code=currency,
        tax_rate=(cost.tax_rate if cost and cost.tax_rate is not None else marketplace.default_tax_rate),
        prices_include_tax=(
            cost.tax_included_in_price
            if cost and cost.tax_included_in_price is not None
            else marketplace.prices_include_tax
        ),
        target_margin_pct=target_margin_pct,
    )
    if cost is not None:
        converted = {
            name: to_selling(getattr(cost, name), cost.currency_code)
            for name in (
                "supplier_cost",
                "shipping_cost",
                "advertising_cost",
                "packaging_cost",
                "other_costs",
                "marketplace_fees",
            )
        }
        missing_fx = any(v is None and getattr(cost, k) is not None for k, v in converted.items())
        inputs.supplier_cost = converted["supplier_cost"]
        for name in ("shipping_cost", "advertising_cost", "packaging_cost", "other_costs"):
            setattr(inputs, name, converted[name] or Decimal(0))
        inputs.returns_allowance_rate = cost.returns_allowance_rate
        inputs.marketplace_fees_override = converted["marketplace_fees"]
    if fee_profile is not None:
        inputs.fees = profit.FeeInputs(
            referral_rate=fee_profile.referral_rate,
            fixed_fee=to_selling(fee_profile.fixed_fee, fee_profile.currency_code) or Decimal(0),
            payment_rate=fee_profile.payment_rate,
            payment_fixed_fee=to_selling(fee_profile.payment_fixed_fee, fee_profile.currency_code) or Decimal(0),
            fulfilment_fee=to_selling(fee_profile.fulfilment_fee, fee_profile.currency_code) or Decimal(0),
        )
    result = profit.calculate(inputs)
    result.fx = fx_used
    if missing_fx:
        result.is_complete = False
        result.missing_inputs.append("exchange_rate")
    if fee_profile is not None:
        result.assumptions.append(f"Fee profile: {fee_profile.name} (effective {fee_profile.effective_from}).")
    if cost is not None:
        result.assumptions.append(f"Cost inputs effective {cost.effective_from:%Y-%m-%d}.")
    return result


def latest_metric(db: Session, listing_id: int, name: str) -> ProductMetric | None:
    return db.scalar(
        select(ProductMetric)
        .where(ProductMetric.listing_id == listing_id, ProductMetric.metric_name == name)
        .order_by(ProductMetric.observed_at.desc())
        .limit(1)
    )


def opportunity(db: Session, organisation_id: uuid.UUID, listing: Listing) -> scoring.OpportunityScore:
    org = db.get(Organisation, organisation_id)
    weights = (org.settings or {}).get("opportunity_weights") if org else None
    estimate = latest_metric(db, listing.id, "estimated_monthly_units")
    offers = latest_metric(db, listing.id, "offer_count")
    since = datetime.now(UTC) - timedelta(days=30)
    prices = list(
        db.scalars(
            select(Observation.price).where(Observation.listing_id == listing.id, Observation.observed_at >= since)
        )
    )
    matched = db.scalar(
        select(func.count())
        .select_from(ProductMatch)
        .where(
            ProductMatch.organisation_id == organisation_id,
            ProductMatch.status != "rejected",
            or_(ProductMatch.source_listing_id == listing.id, ProductMatch.target_listing_id == listing.id),
        )
    )
    image_count = db.scalar(select(func.count()).select_from(Image).where(Image.listing_id == listing.id)) or 0
    margin = listing_profit(db, organisation_id, listing).margin_pct
    cost = latest_cost(db, organisation_id, listing)
    attrs = (cost.assessment if cost else None) or {}
    components = [
        scoring.demand_score(
            listing.sales_rank,
            estimate.numeric_value if estimate and estimate.measurement_type != "unavailable" else None,
        ),
        scoring.competition_score(
            listing.review_count,
            int(offers.numeric_value) if offers and offers.numeric_value is not None else None,
            matched,
        ),
        scoring.margin_score(margin),
        scoring.stability_score(prices),
        scoring.quality_score(listing.rating, image_count, bool(listing.description)),
        scoring.stock_score(listing.stock_status),
        scoring.user_component("supplier_reliability", attrs.get("supplier_reliability")),
        scoring.user_component("delivery_speed", attrs.get("delivery_speed")),
        scoring.user_component("compliance_risk", attrs.get("compliance_risk"), invert=True),
    ]
    return scoring.combine(components, weights)
