from __future__ import annotations

import uuid
from dataclasses import asdict as _asdict
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.core.security import Permission, has_permission
from app.models import ExchangeRate, FeeProfile, Listing, Marketplace, ProjectProduct, User
from app.schemas.common import ORM, jsonable, listing_summary
from app.services import analysis, analytics, fx, profit


def asdict(obj) -> dict:
    return jsonable(_asdict(obj))


router = APIRouter(tags=["analytics"])


@router.get("/analytics/overview")
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return analytics.overview(
        db, user.organisation_id, include_own=has_permission(user.role, Permission.VIEW_OWN_ACCOUNT_DATA)
    )


@router.get("/analytics/own-account")
def own_account(
    user: User = Depends(require(Permission.VIEW_OWN_ACCOUNT_DATA)),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
):
    return {"accounts": analytics.own_account_summary(db, user.organisation_id, days)}


@router.post("/analytics/profit/calculate")
def calculate(body: profit.CostInputs, user: User = Depends(current_user)):
    """Stateless profit calculator (what-if analysis)."""
    return asdict(profit.calculate(body))


@router.get("/analytics/profit")
def profit_for_listings(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    listing_id: list[int] = Query(default_factory=list, max_length=200),
    project_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = select(Listing).where(Listing.organisation_id == user.organisation_id)
    if not has_permission(user.role, Permission.VIEW_OWN_ACCOUNT_DATA):
        stmt = stmt.where(Listing.source_type != "own_account")
    if listing_id:
        stmt = stmt.where(Listing.id.in_(listing_id))
    elif project_id:
        stmt = stmt.where(
            Listing.id.in_(select(ProjectProduct.listing_id).where(ProjectProduct.project_id == project_id))
        )
    else:
        raise AppError("Provide listing_id or project_id")
    return [
        {
            "listing": listing_summary(listing),
            "profit": asdict(analysis.listing_profit(db, user.organisation_id, listing)),
        }
        for listing in db.scalars(stmt.limit(limit))
    ]


@router.get("/analytics/opportunities")
def opportunities(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    project_id: uuid.UUID | None = None,
    marketplace: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    candidates: int = Query(default=300, ge=1, le=2000),
):
    """Score a bounded candidate set (watched / project / best-ranked first) and return
    the top results with component scores, confidence and missing-data warnings."""
    stmt = select(Listing).where(Listing.organisation_id == user.organisation_id, Listing.source_type != "own_account")
    if project_id:
        stmt = stmt.where(
            Listing.id.in_(select(ProjectProduct.listing_id).where(ProjectProduct.project_id == project_id))
        )
    if marketplace:
        stmt = stmt.join(Marketplace, Marketplace.id == Listing.marketplace_id).where(Marketplace.code == marketplace)
    rows = db.scalars(
        stmt.order_by(
            Listing.is_watched.desc(), Listing.sales_rank.asc().nulls_last(), Listing.updated_at.desc()
        ).limit(candidates)
    ).all()
    scored = []
    for listing in rows:
        s = analysis.opportunity(db, user.organisation_id, listing)
        scored.append(
            {
                "listing": listing_summary(listing),
                "score": s.score,
                "confidence": s.confidence,
                "missing": s.missing,
                "components": [asdict(c) for c in s.components],
            }
        )
    scored.sort(key=lambda x: (x["score"], x["confidence"]), reverse=True)
    return {"items": scored[:limit], "candidates_scored": len(rows)}


# ------------------------------------------------------------ fee profiles
class FeeProfileIn(BaseModel):
    marketplace_code: str
    name: str = Field(min_length=1, max_length=120)
    category_pattern: str | None = None
    referral_rate: Decimal = Field(default=Decimal(0), ge=0, le=1)
    fixed_fee: Decimal = Field(default=Decimal(0), ge=0)
    payment_rate: Decimal = Field(default=Decimal(0), ge=0, le=1)
    payment_fixed_fee: Decimal = Field(default=Decimal(0), ge=0)
    fulfilment_fee: Decimal = Field(default=Decimal(0), ge=0)
    currency_code: str = Field(pattern="^[A-Z]{3}$")
    notes: str | None = None
    effective_from: date | None = None


class FeeProfileOut(ORM):
    id: int
    marketplace_id: int
    name: str
    category_pattern: str | None
    referral_rate: Decimal
    fixed_fee: Decimal
    payment_rate: Decimal
    payment_fixed_fee: Decimal
    fulfilment_fee: Decimal
    currency_code: str
    notes: str | None
    effective_from: date
    is_global: bool = False


@router.get("/fee-profiles", response_model=list[FeeProfileOut])
def list_fee_profiles(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(FeeProfile)
        .where(or_(FeeProfile.organisation_id == user.organisation_id, FeeProfile.organisation_id.is_(None)))
        .order_by(FeeProfile.marketplace_id, FeeProfile.effective_from.desc())
    ).all()
    out = []
    for r in rows:
        o = FeeProfileOut.model_validate(r)
        o.is_global = r.organisation_id is None
        out.append(o)
    return out


@router.post("/fee-profiles", response_model=FeeProfileOut, status_code=201)
def create_fee_profile(
    body: FeeProfileIn, user: User = Depends(require(Permission.EDIT_COSTS)), db: Session = Depends(get_db)
):
    mp = db.scalar(select(Marketplace).where(Marketplace.code == body.marketplace_code))
    if mp is None:
        raise AppError("Unknown marketplace")
    data = body.model_dump(exclude={"marketplace_code", "effective_from"})
    fp = FeeProfile(organisation_id=user.organisation_id, marketplace_id=mp.id, **data)
    if body.effective_from:
        fp.effective_from = body.effective_from
    db.add(fp)
    db.commit()
    db.refresh(fp)
    return fp


@router.delete("/fee-profiles/{profile_id}", status_code=204)
def delete_fee_profile(
    profile_id: int, user: User = Depends(require(Permission.EDIT_COSTS)), db: Session = Depends(get_db)
):
    fp = db.get(FeeProfile, profile_id)
    if fp is None or fp.organisation_id != user.organisation_id:
        raise NotFound("Fee profile not found")
    db.delete(fp)
    db.commit()


# ---------------------------------------------------------- exchange rates
class RateIn(BaseModel):
    base_currency: str = Field(pattern="^[A-Z]{3}$")
    quote_currency: str = Field(pattern="^[A-Z]{3}$")
    rate: Decimal = Field(gt=0)
    effective_at: datetime


class RateOut(ORM):
    id: int
    source: str
    base_currency: str
    quote_currency: str
    rate: Decimal
    effective_at: datetime


@router.get("/exchange-rates", response_model=list[RateOut])
def list_rates(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    base: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    stmt = select(ExchangeRate)
    if base:
        stmt = stmt.where(ExchangeRate.base_currency == base.upper())
    return db.scalars(stmt.order_by(ExchangeRate.effective_at.desc()).limit(limit)).all()


@router.post("/exchange-rates", response_model=RateOut, status_code=201)
def add_rate(body: RateIn, user: User = Depends(require(Permission.EDIT_COSTS)), db: Session = Depends(get_db)):
    fx.store_rate(
        db, f"manual:{user.organisation_id}", body.base_currency, body.quote_currency, body.rate, body.effective_at
    )
    db.commit()
    return db.scalar(
        select(ExchangeRate).where(
            ExchangeRate.source == f"manual:{user.organisation_id}",
            ExchangeRate.base_currency == body.base_currency,
            ExchangeRate.quote_currency == body.quote_currency,
            ExchangeRate.effective_at == body.effective_at,
        )
    )


@router.get("/exchange-rates/convert")
def convert(
    amount: Decimal,
    base: str,
    quote: str,
    at: datetime | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rate = fx.find_rate(db, base, quote, at)
    if rate is None:
        raise NotFound(f"No exchange rate available for {base.upper()}->{quote.upper()}")
    return {
        "amount": str(amount),
        "base": base.upper(),
        "quote": quote.upper(),
        "converted": str((amount * rate.rate).quantize(Decimal("0.01"))),
        "rate": str(rate.rate),
        "source": rate.source,
        "effective_at": rate.effective_at.isoformat(),
    }
