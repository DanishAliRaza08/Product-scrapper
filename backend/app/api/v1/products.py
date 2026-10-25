from __future__ import annotations

import uuid
from dataclasses import asdict as _asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user, require
from app.core.db import get_db
from app.core.errors import AppError, Forbidden, NotFound
from app.core.security import Permission, has_permission
from app.models import (
    Listing,
    Observation,
    ProductCost,
    ProductMatch,
    ProductMetric,
    ProductSource,
    User,
)
from app.schemas.common import ORM, ListingDetail, Page, jsonable, listing_summary
from app.services import analysis, matching
from app.services.products import ProductFilters, build_query, estimate_count, paginate


def asdict(obj) -> dict:
    return jsonable(_asdict(obj))


router = APIRouter(tags=["products"])


def can_view_own(user: User) -> bool:
    return has_permission(user.role, Permission.VIEW_OWN_ACCOUNT_DATA)


def get_listing(db: Session, user: User, listing_id: int) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None or listing.organisation_id != user.organisation_id:
        raise NotFound("Product not found")
    if listing.source_type == "own_account" and not can_view_own(user):
        raise Forbidden("Own-account data requires additional permission")
    return listing


@router.get("/products", response_model=Page[dict])
def search_products(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, max_length=200),
    marketplace: list[str] | None = Query(default=None),
    country: list[str] | None = Query(default=None),
    platform: list[str] | None = Query(default=None),
    source_type: list[str] | None = Query(default=None),
    stock_status: list[str] | None = Query(default=None),
    category: str | None = None,
    brand: str | None = None,
    seller: str | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    currency: str | None = None,
    rating_min: Decimal | None = None,
    rank_max: int | None = None,
    project_id: uuid.UUID | None = None,
    tag: str | None = None,
    canonical_product_id: int | None = None,
    has_gtin: bool | None = None,
    watched: bool | None = None,
    sort: str = "updated",
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    with_total: bool = False,
) -> Page[dict]:
    filters = ProductFilters(
        q=q,
        marketplace=marketplace,
        country=country,
        platform=platform,
        source_type=source_type,
        stock_status=stock_status,
        category=category,
        brand=brand,
        seller=seller,
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        rating_min=rating_min,
        rank_max=rank_max,
        project_id=project_id,
        tag=tag,
        canonical_product_id=canonical_product_id,
        has_gtin=has_gtin,
        watched=watched,
        sort=sort,
    )
    stmt = build_query(user.organisation_id, filters, can_view_own=can_view_own(user))
    rows, nxt = paginate(db, stmt, sort, cursor, limit)
    total, is_estimate = estimate_count(db, stmt) if with_total else (None, False)
    return Page(items=[listing_summary(r) for r in rows], next_cursor=nxt, total=total, total_is_estimate=is_estimate)


@router.get("/products/{listing_id}", response_model=ListingDetail)
def product_detail(listing_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    listing = get_listing(db, user, listing_id)
    db.refresh(listing)
    listing = db.scalar(
        select(Listing)
        .options(selectinload(Listing.variations), selectinload(Listing.images))
        .where(Listing.id == listing_id)
    )
    data = {
        **listing_summary(listing),
        **{
            k: getattr(listing, k)
            for k in ListingDetail.model_fields
            if k not in ("marketplace_code", "country_code")
            and hasattr(listing, k)
            and k not in ("variations", "images")
        },
    }
    data["variations"] = listing.variations
    data["images"] = listing.images
    return ListingDetail.model_validate(data)


class ProductPatch(BaseModel):
    is_watched: bool | None = None
    refresh_priority: int | None = Field(default=None, ge=0, le=100)


@router.patch("/products/{listing_id}", response_model=dict)
def update_product(
    listing_id: int,
    body: ProductPatch,
    user: User = Depends(require(Permission.RUN_JOBS)),
    db: Session = Depends(get_db),
):
    listing = get_listing(db, user, listing_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(listing, k, v)
    db.commit()
    db.refresh(listing)
    return listing_summary(listing)


class ObservationOut(ORM):
    observed_at: datetime
    price: Decimal | None
    currency_code: str | None
    shipping_price: Decimal | None
    stock_status: str | None
    stock_quantity: int | None
    sales_rank: int | None
    rating: Decimal | None
    review_count: int | None


@router.get("/products/{listing_id}/history", response_model=list[ObservationOut])
def product_history(
    listing_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    days: int = Query(default=90, ge=1, le=3650),
    limit: int = Query(default=1000, ge=1, le=10000),
):
    get_listing(db, user, listing_id)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(
        select(Observation)
        .where(Observation.listing_id == listing_id, Observation.observed_at >= since)
        .order_by(Observation.observed_at.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


class MetricOut(ORM):
    metric_name: str
    numeric_value: Decimal | None
    unit: str | None
    measurement_type: str
    methodology: str | None
    confidence: Decimal | None
    source: str
    period_start: datetime | None
    period_end: datetime | None
    observed_at: datetime


@router.get("/products/{listing_id}/metrics", response_model=list[MetricOut])
def product_metrics(
    listing_id: int, user: User = Depends(current_user), db: Session = Depends(get_db), latest_only: bool = True
):
    get_listing(db, user, listing_id)
    stmt = select(ProductMetric).where(ProductMetric.listing_id == listing_id)
    if latest_only:
        stmt = stmt.distinct(ProductMetric.metric_name).order_by(
            ProductMetric.metric_name, ProductMetric.observed_at.desc()
        )
    else:
        stmt = stmt.order_by(ProductMetric.observed_at.desc()).limit(500)
    return db.scalars(stmt).all()


class SourceOut(ORM):
    field_name: str
    source_type: str
    connector: str
    connector_version: str | None
    endpoint: str | None
    source_reference: str | None
    retrieved_at: datetime


@router.get("/products/{listing_id}/sources", response_model=list[SourceOut])
def product_sources(listing_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_listing(db, user, listing_id)
    return db.scalars(
        select(ProductSource).where(ProductSource.listing_id == listing_id).order_by(ProductSource.field_name)
    ).all()


# ------------------------------------------------------------ profit & costs
class CostIn(BaseModel):
    currency_code: str = Field(pattern="^[A-Z]{3}$")
    supplier_cost: Decimal = Field(ge=0)
    shipping_cost: Decimal = Field(default=Decimal(0), ge=0)
    marketplace_fees: Decimal | None = Field(default=None, ge=0)
    advertising_cost: Decimal = Field(default=Decimal(0), ge=0)
    packaging_cost: Decimal = Field(default=Decimal(0), ge=0)
    returns_allowance_rate: Decimal = Field(default=Decimal(0), ge=0, le=1)
    other_costs: Decimal = Field(default=Decimal(0), ge=0)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    tax_included_in_price: bool | None = None
    fee_profile_id: int | None = None
    assessment: dict[str, float] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=2000)
    apply_to_canonical: bool = False


class CostOut(ORM):
    id: int
    listing_id: int | None
    canonical_product_id: int | None
    currency_code: str
    supplier_cost: Decimal
    shipping_cost: Decimal
    marketplace_fees: Decimal | None
    advertising_cost: Decimal
    packaging_cost: Decimal
    returns_allowance_rate: Decimal
    other_costs: Decimal
    tax_rate: Decimal | None
    tax_included_in_price: bool | None
    fee_profile_id: int | None
    assessment: dict
    notes: str | None
    effective_from: datetime


@router.get("/products/{listing_id}/costs", response_model=list[CostOut])
def list_costs(listing_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    listing = get_listing(db, user, listing_id)
    conditions = [ProductCost.listing_id == listing.id]
    if listing.canonical_product_id:
        conditions.append(ProductCost.canonical_product_id == listing.canonical_product_id)
    return db.scalars(
        select(ProductCost)
        .where(ProductCost.organisation_id == user.organisation_id, or_(*conditions))
        .order_by(ProductCost.effective_from.desc())
    ).all()


@router.post("/products/{listing_id}/costs", response_model=CostOut, status_code=201)
def add_cost(
    listing_id: int, body: CostIn, user: User = Depends(require(Permission.EDIT_COSTS)), db: Session = Depends(get_db)
):
    """Costs are append-only and time-stamped, so historical calculations stay reproducible."""
    listing = get_listing(db, user, listing_id)
    allowed = {"supplier_reliability", "delivery_speed", "compliance_risk"}
    if set(body.assessment) - allowed:
        raise AppError(f"Assessment keys must be among {sorted(allowed)}")
    if body.apply_to_canonical and not listing.canonical_product_id:
        raise AppError("Listing is not linked to a canonical product yet")
    data = body.model_dump(exclude={"apply_to_canonical"})
    cost = ProductCost(
        organisation_id=user.organisation_id,
        user_id=user.id,
        listing_id=None if body.apply_to_canonical else listing.id,
        canonical_product_id=listing.canonical_product_id if body.apply_to_canonical else None,
        **data,
    )
    db.add(cost)
    db.commit()
    db.refresh(cost)
    return cost


@router.get("/products/{listing_id}/profit")
def product_profit(
    listing_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    selling_price: Decimal | None = None,
    target_margin_pct: Decimal | None = None,
):
    listing = get_listing(db, user, listing_id)
    return asdict(
        analysis.listing_profit(
            db, user.organisation_id, listing, selling_price=selling_price, target_margin_pct=target_margin_pct
        )
    )


@router.get("/products/{listing_id}/opportunity")
def product_opportunity(listing_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return asdict(analysis.opportunity(db, user.organisation_id, get_listing(db, user, listing_id)))


# ----------------------------------------------------------------- matching
class MatchOut(ORM):
    id: int
    source_listing_id: int
    target_listing_id: int
    match_method: str
    score: Decimal
    evidence: dict
    status: str
    reviewed_at: datetime | None
    created_at: datetime
    source: dict | None = None
    target: dict | None = None


def match_out(m: ProductMatch) -> MatchOut:
    out = MatchOut.model_validate(m)
    out.source = listing_summary(m.source_listing)
    out.target = listing_summary(m.target_listing)
    return out


@router.post("/products/{listing_id}/matches/suggest", response_model=list[MatchOut])
def suggest_matches(listing_id: int, user: User = Depends(require(Permission.RUN_JOBS)), db: Session = Depends(get_db)):
    get_listing(db, user, listing_id)
    matching.find_candidates(db, user.organisation_id, listing_id)
    db.commit()
    return [
        match_out(m)
        for m in db.scalars(
            select(ProductMatch)
            .where(or_(ProductMatch.source_listing_id == listing_id, ProductMatch.target_listing_id == listing_id))
            .order_by(ProductMatch.score.desc())
        )
    ]


@router.get("/matches", response_model=Page[MatchOut])
def list_matches(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    status: str = "candidate",
    min_score: Decimal = Decimal("0"),
    listing_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
):
    stmt = select(ProductMatch).where(
        ProductMatch.organisation_id == user.organisation_id,
        ProductMatch.status == status,
        ProductMatch.score >= min_score,
    )
    if listing_id:
        stmt = stmt.where(
            or_(ProductMatch.source_listing_id == listing_id, ProductMatch.target_listing_id == listing_id)
        )
    rows = list(db.scalars(stmt.order_by(ProductMatch.score.desc(), ProductMatch.id).offset(offset).limit(limit + 1)))
    return Page(
        items=[match_out(m) for m in rows[:limit]], next_cursor=str(offset + limit) if len(rows) > limit else None
    )


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(confirmed|rejected)$")


@router.post("/matches/{match_id}/review", response_model=MatchOut)
def review_match(
    match_id: int,
    body: ReviewIn,
    user: User = Depends(require(Permission.REVIEW_MATCHES)),
    db: Session = Depends(get_db),
):
    m = matching.review(db, user.organisation_id, match_id, body.decision, user.id)
    db.commit()
    db.refresh(m)
    return match_out(m)


class CompareIn(BaseModel):
    listing_ids: list[int] = Field(min_length=2, max_length=10)
    currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")


@router.post("/products/compare")
def compare(body: CompareIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Side-by-side comparison with prices optionally converted to one currency
    (original amounts are always returned too)."""
    from app.services.fx import find_rate

    out = []
    for lid in body.listing_ids:
        listing = get_listing(db, user, lid)
        item = listing_summary(listing)
        item["converted_price"] = None
        if body.currency and listing.current_price is not None and listing.currency_code:
            rate = find_rate(db, listing.currency_code, body.currency)
            if rate:
                item["converted_price"] = str((listing.current_price * rate.rate).quantize(Decimal("0.01")))
                item["fx"] = {
                    "rate": str(rate.rate),
                    "source": rate.source,
                    "effective_at": rate.effective_at.isoformat(),
                }
        p = analysis.listing_profit(db, user.organisation_id, listing)
        item["profit"] = {
            "profit": p.profit,
            "margin_pct": p.margin_pct,
            "is_complete": p.is_complete,
            "missing_inputs": p.missing_inputs,
        }
        item["metrics"] = [
            {"name": m.metric_name, "value": m.numeric_value, "type": m.measurement_type, "unit": m.unit}
            for m in db.scalars(
                select(ProductMetric)
                .where(ProductMetric.listing_id == lid)
                .distinct(ProductMetric.metric_name)
                .order_by(ProductMetric.metric_name, ProductMetric.observed_at.desc())
            )
        ]
        out.append(item)
    return jsonable({"currency": body.currency, "items": out})
