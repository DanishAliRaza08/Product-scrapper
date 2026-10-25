from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    total: int | None = None
    total_is_estimate: bool = False


class MarketplaceOut(ORM):
    id: int
    code: str
    platform: str
    name: str
    country_code: str
    currency_code: str
    prices_include_tax: bool
    default_tax_rate: Decimal


class ListingSummary(ORM):
    id: int
    marketplace_code: str
    country_code: str
    external_listing_id: str
    seller_sku: str | None = None
    title: str
    brand: str | None = None
    category_path: str | None = None
    main_image_url: str | None = None
    product_url: str
    currency_code: str | None = None
    current_price: Decimal | None = None
    shipping_price: Decimal | None = None
    stock_status: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    sales_rank: int | None = None
    source_type: str
    is_watched: bool
    canonical_product_id: int | None = None
    last_seen_at: datetime
    last_changed_at: datetime | None = None


class VariationOut(ORM):
    id: int
    external_variation_id: str | None
    variation_sku: str | None
    size: str | None
    colour: str | None
    price: Decimal | None
    currency_code: str | None
    stock_status: str | None
    attributes: dict


class ImageOut(ORM):
    id: int
    image_url: str
    image_type: str | None
    display_order: int


class ListingDetail(ListingSummary):
    parent_listing_id: str | None = None
    asin: str | None = None
    gtin: str | None = None
    mpn: str | None = None
    description: str | None = None
    category_id: str | None = None
    original_price: Decimal | None = None
    stock_quantity: int | None = None
    stock_quantity_type: str | None = None
    condition: str | None = None
    rank_category: str | None = None
    seller_external_id: str | None = None
    seller_name: str | None = None
    fulfilment_type: str | None = None
    attributes: dict = Field(default_factory=dict)
    account_id: uuid.UUID | None = None
    first_seen_at: datetime
    variations: list[VariationOut] = Field(default_factory=list)
    images: list[ImageOut] = Field(default_factory=list)


def listing_summary(listing) -> dict:
    data = ListingSummary.model_validate(
        {
            **{k: getattr(listing, k) for k in ListingSummary.model_fields if hasattr(listing, k)},
            "marketplace_code": listing.marketplace.code,
            "country_code": listing.marketplace.country_code,
        }
    )
    return data.model_dump()


def jsonable(value):
    """Recursively convert dataclass/asdict output for JSON: money stays exact as strings."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
