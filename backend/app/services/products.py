"""Product search with server-side filtering and keyset (cursor) pagination."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Select, func, literal, or_, select, tuple_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.pagination import decode_cursor, encode_cursor
from app.models import JobItem, Listing, Marketplace, ProjectProduct

# name -> (column, direction, nullable)
SORTS = {
    "updated": (Listing.updated_at, "desc", False),
    "price_asc": (Listing.current_price, "asc", True),
    "price_desc": (Listing.current_price, "desc", True),
    "rank": (Listing.sales_rank, "asc", True),
    "reviews": (Listing.review_count, "desc", True),
    "title": (Listing.title, "asc", False),
}


class ProductFilters(BaseModel):
    q: str | None = Field(default=None, max_length=200)
    marketplace: list[str] | None = None
    country: list[str] | None = None
    platform: list[str] | None = None
    source_type: list[str] | None = None
    stock_status: list[str] | None = None
    category: str | None = None
    brand: str | None = None
    seller: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    currency: str | None = None
    rating_min: Decimal | None = None
    rank_max: int | None = None
    project_id: uuid.UUID | None = None
    tag: str | None = None
    canonical_product_id: int | None = None
    job_id: uuid.UUID | None = None
    has_gtin: bool | None = None
    watched: bool | None = None
    updated_since: datetime | None = None
    include_own_account: bool = True
    sort: str = "updated"


def build_query(organisation_id: uuid.UUID, f: ProductFilters, *, can_view_own: bool) -> Select:
    stmt = (
        select(Listing)
        .join(Marketplace, Marketplace.id == Listing.marketplace_id)
        .where(Listing.organisation_id == organisation_id)
    )
    if not can_view_own or not f.include_own_account:
        stmt = stmt.where(Listing.source_type != "own_account")
    if f.q:
        ts = func.websearch_to_tsquery("simple", f.q)
        stmt = stmt.where(or_(Listing.search_vector.op("@@")(ts), Listing.title.ilike(f"%{_escape_like(f.q)}%")))
    if f.marketplace:
        stmt = stmt.where(Marketplace.code.in_(f.marketplace))
    if f.country:
        stmt = stmt.where(Marketplace.country_code.in_([c.upper() for c in f.country]))
    if f.platform:
        stmt = stmt.where(Marketplace.platform.in_(f.platform))
    if f.source_type:
        stmt = stmt.where(Listing.source_type.in_(f.source_type))
    if f.stock_status:
        stmt = stmt.where(Listing.stock_status.in_(f.stock_status))
    if f.category:
        stmt = stmt.where(Listing.category_path.ilike(f"%{_escape_like(f.category)}%"))
    if f.brand:
        stmt = stmt.where(func.lower(Listing.brand) == f.brand.lower())
    if f.seller:
        stmt = stmt.where(
            or_(Listing.seller_external_id == f.seller, Listing.seller_name.ilike(f"%{_escape_like(f.seller)}%"))
        )
    if f.price_min is not None:
        stmt = stmt.where(Listing.current_price >= f.price_min)
    if f.price_max is not None:
        stmt = stmt.where(Listing.current_price <= f.price_max)
    if f.currency:
        stmt = stmt.where(Listing.currency_code == f.currency.upper())
    if f.rating_min is not None:
        stmt = stmt.where(Listing.rating >= f.rating_min)
    if f.rank_max is not None:
        stmt = stmt.where(Listing.sales_rank <= f.rank_max)
    if f.project_id or f.tag:
        pp = select(ProjectProduct.listing_id)
        if f.project_id:
            pp = pp.where(ProjectProduct.project_id == f.project_id)
        if f.tag:
            pp = pp.where(ProjectProduct.tags.any(f.tag))
        stmt = stmt.where(Listing.id.in_(pp))
    if f.job_id is not None:
        stmt = stmt.where(
            Listing.id.in_(
                select(JobItem.listing_id).where(JobItem.job_id == f.job_id, JobItem.listing_id.is_not(None))
            )
        )
    if f.canonical_product_id is not None:
        stmt = stmt.where(Listing.canonical_product_id == f.canonical_product_id)
    if f.has_gtin is not None:
        stmt = stmt.where(Listing.gtin.is_not(None) if f.has_gtin else Listing.gtin.is_(None))
    if f.watched is not None:
        stmt = stmt.where(Listing.is_watched.is_(f.watched))
    if f.updated_since is not None:
        stmt = stmt.where(Listing.updated_at >= f.updated_since)
    return stmt


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _serialise(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _parse(column, value: Any) -> Any:
    if value is None:
        return None
    if column is Listing.updated_at:
        return datetime.fromisoformat(value)
    if column is Listing.current_price:
        return Decimal(value)
    return value


def paginate(db: Session, stmt: Select, sort: str, cursor: str | None, limit: int) -> tuple[list[Listing], str | None]:
    """Keyset pagination on (sort column, id) using row-value comparisons, which
    PostgreSQL serves straight from the (organisation_id, column, id) indexes.

    Rows whose sort value is NULL (e.g. no price) are returned after all non-NULL
    rows, in id order, as a second phase - never skipped and never duplicated."""
    if sort not in SORTS:
        raise AppError(f"Unknown sort {sort!r}; use one of {', '.join(SORTS)}", code="invalid_sort")
    column, direction, nullable = SORTS[sort]
    asc = direction == "asc"
    c = decode_cursor(cursor)
    if c is not None and c.get("s") != sort:
        raise AppError("Cursor does not match the requested sort", code="invalid_cursor")

    rows: list[Listing] = []
    in_null_phase = c is not None and c.get("n") is True
    if not in_null_phase:
        q = stmt.where(column.is_not(None)) if nullable else stmt
        if c is not None:
            key = tuple_(column, Listing.id)
            bound = tuple_(literal(_parse(column, c["v"]), column.type), literal(int(c["id"])))
            q = q.where(key > bound if asc else key < bound)
        order = (column.asc(), Listing.id.asc()) if asc else (column.desc(), Listing.id.desc())
        rows = list(db.scalars(q.order_by(*order).limit(limit + 1)).unique())
        if len(rows) > limit:
            last = rows[limit - 1]
            return rows[:limit], encode_cursor({"s": sort, "v": _serialise(getattr(last, column.key)), "id": last.id})
        if not nullable:
            return rows, None
    after_id = int(c["id"]) if in_null_phase and c is not None else 0
    q = stmt.where(column.is_(None), Listing.id > after_id).order_by(Listing.id.asc())
    rows += list(db.scalars(q.limit(limit - len(rows) + 1)).unique())
    if len(rows) > limit:
        last = rows[limit - 1]
        return rows[:limit], encode_cursor({"s": sort, "n": True, "id": last.id})
    return rows, None


def estimate_count(db: Session, stmt: Select, exact_threshold: int = 10_000) -> tuple[int, bool]:
    """Exact count for small result sets; capped count for large ones (counting
    millions of rows on every page request is wasteful)."""
    capped = db.scalar(
        select(func.count()).select_from(stmt.with_only_columns(Listing.id).limit(exact_threshold + 1).subquery())
    )
    return (min(capped or 0, exact_threshold), (capped or 0) > exact_threshold)
