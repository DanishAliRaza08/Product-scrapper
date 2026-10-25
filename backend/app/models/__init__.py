"""ORM models. The schema itself is owned by the SQL migrations in ``migrations/sql``;
these mappings mirror it and rely on server-side defaults."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

NOW = text("now()")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _bigint_pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=NOW)


Money = Numeric(14, 4)


class Organisation(Base):
    __tablename__ = "organisations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    base_currency: Mapped[str] = mapped_column(String(3), server_default="GBP")
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    email: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="researcher")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    export_row_limit: Mapped[int | None] = mapped_column(Integer)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()

    organisation: Mapped[Organisation] = relationship(lazy="joined")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = _bigint_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    client_type: Mapped[str] = mapped_column(Text, server_default="web")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _created()


class Marketplace(Base):
    __tablename__ = "marketplaces"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    platform: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str] = mapped_column(String(2))
    currency_code: Mapped[str] = mapped_column(String(3))
    prices_include_tax: Mapped[bool] = mapped_column(Boolean)
    default_tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    is_enabled: Mapped[bool] = mapped_column(Boolean)


class MarketplaceAccount(Base):
    __tablename__ = "marketplace_accounts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    account_name: Mapped[str] = mapped_column(Text)
    external_account_id: Mapped[str | None] = mapped_column(Text)
    connection_status: Mapped[str] = mapped_column(Text, server_default="pending")
    granted_scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    credential_reference: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created()

    marketplace: Mapped[Marketplace] = relationship(lazy="joined")


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("marketplace_accounts.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoints"
    id: Mapped[int] = _bigint_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("marketplace_accounts.id"))
    stream: Mapped[str] = mapped_column(Text)
    cursor: Mapped[str | None] = mapped_column(Text)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)


class CanonicalProduct(Base):
    __tablename__ = "canonical_products"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    gtin: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    model_number: Mapped[str | None] = mapped_column(Text)
    normalised_title: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    match_status: Mapped[str] = mapped_column(Text, server_default="unverified")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Listing(Base):
    __tablename__ = "marketplace_listings"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    canonical_product_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_products.id"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("marketplace_accounts.id"))
    external_listing_id: Mapped[str] = mapped_column(Text)
    parent_listing_id: Mapped[str | None] = mapped_column(Text)
    seller_sku: Mapped[str | None] = mapped_column(Text)
    asin: Mapped[str | None] = mapped_column(Text)
    gtin: Mapped[str | None] = mapped_column(Text)
    mpn: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    category_path: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[str | None] = mapped_column(Text)
    product_url: Mapped[str] = mapped_column(Text)
    main_image_url: Mapped[str | None] = mapped_column(Text)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    current_price: Mapped[Decimal | None] = mapped_column(Money)
    original_price: Mapped[Decimal | None] = mapped_column(Money)
    shipping_price: Mapped[Decimal | None] = mapped_column(Money)
    stock_status: Mapped[str | None] = mapped_column(Text)
    stock_quantity: Mapped[int | None] = mapped_column(Integer)
    stock_quantity_type: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    review_count: Mapped[int | None] = mapped_column(Integer)
    sales_rank: Mapped[int | None] = mapped_column(BigInteger)
    rank_category: Mapped[str | None] = mapped_column(Text)
    seller_external_id: Mapped[str | None] = mapped_column(Text)
    seller_name: Mapped[str | None] = mapped_column(Text)
    fulfilment_type: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    source_type: Mapped[str] = mapped_column(Text)
    is_watched: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    refresh_priority: Mapped[int] = mapped_column(SmallInteger, server_default="50")
    content_hash: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = _created()
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    # Generated column (see migration); Computed keeps it out of INSERT/UPDATE.
    search_vector = mapped_column(TSVECTOR, Computed("search_vector", persisted=True), deferred=True)

    marketplace: Mapped[Marketplace] = relationship(lazy="joined")
    variations: Mapped[list["Variation"]] = relationship(back_populates="listing", order_by="Variation.id")
    images: Mapped[list["Image"]] = relationship(order_by="Image.display_order")


class Variation(Base):
    __tablename__ = "product_variations"
    id: Mapped[int] = _bigint_pk()
    listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id"))
    external_variation_id: Mapped[str | None] = mapped_column(Text)
    variation_sku: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    colour: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Money)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    stock_status: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    updated_at: Mapped[datetime] = _created()

    listing: Mapped[Listing] = relationship(back_populates="variations")


class Image(Base):
    __tablename__ = "product_images"
    id: Mapped[int] = _bigint_pk()
    listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id"))
    variation_id: Mapped[int | None] = mapped_column(ForeignKey("product_variations.id"))
    image_url: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)
    image_type: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    display_order: Mapped[int] = mapped_column(Integer, server_default="0")


class ProductSource(Base):
    __tablename__ = "product_sources"
    id: Mapped[int] = _bigint_pk()
    listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id"))
    field_name: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)
    connector: Mapped[str] = mapped_column(Text)
    connector_version: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = _created()


class ProductMatch(Base):
    __tablename__ = "product_matches"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    source_listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id"))
    target_listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id"))
    match_method: Mapped[str] = mapped_column(Text)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    evidence: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text, server_default="candidate")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()

    source_listing: Mapped[Listing] = relationship(foreign_keys=[source_listing_id])
    target_listing: Mapped[Listing] = relationship(foreign_keys=[target_listing_id])


class SellerOrder(Base):
    __tablename__ = "seller_orders"
    id: Mapped[int] = _bigint_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("marketplace_accounts.id"))
    external_order_id: Mapped[str] = mapped_column(Text)
    order_status: Mapped[str | None] = mapped_column(Text)
    order_currency: Mapped[str | None] = mapped_column(String(3))
    order_total: Mapped[Decimal | None] = mapped_column(Money)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_reference: Mapped[str | None] = mapped_column(Text)


class SellerOrderItem(Base):
    __tablename__ = "seller_order_items"
    id: Mapped[int] = _bigint_pk()
    order_id: Mapped[int] = mapped_column(ForeignKey("seller_orders.id"))
    external_order_item_id: Mapped[str | None] = mapped_column(Text)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("marketplace_listings.id"))
    seller_sku: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer, server_default="1")
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    item_tax: Mapped[Decimal | None] = mapped_column(Money)
    marketplace_fees: Mapped[Decimal | None] = mapped_column(Money)
    refunded_amount: Mapped[Decimal] = mapped_column(Money, server_default="0")
    currency_code: Mapped[str | None] = mapped_column(String(3))


class ResearchProject(Base):
    __tablename__ = "research_projects"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class CompetitorShop(Base):
    __tablename__ = "competitor_shops"
    id: Mapped[int] = _bigint_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_projects.id"))
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    shop_url: Mapped[str] = mapped_column(Text)
    external_seller_id: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class ProjectProduct(Base):
    __tablename__ = "project_products"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_projects.id"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id"), primary_key=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    notes: Mapped[str | None] = mapped_column(Text)
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = _created()


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_projects.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSONB)
    schedule_cron: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Job(Base):
    __tablename__ = "extraction_jobs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_projects.id"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("marketplace_accounts.id"))
    marketplace_id: Mapped[int | None] = mapped_column(ForeignKey("marketplaces.id"))
    job_type: Mapped[str] = mapped_column(Text, server_default="research")
    input_type: Mapped[str] = mapped_column(Text)
    input_url: Mapped[str | None] = mapped_column(Text)
    search_keyword: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    job_status: Mapped[str] = mapped_column(Text, server_default="pending")
    configuration: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    discovery_cursor: Mapped[str | None] = mapped_column(Text)
    discovery_complete: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source_limit_note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    products_discovered: Mapped[int] = mapped_column(BigInteger, server_default="0")
    products_processed: Mapped[int] = mapped_column(BigInteger, server_default="0")
    products_failed: Mapped[int] = mapped_column(BigInteger, server_default="0")
    products_skipped: Mapped[int] = mapped_column(BigInteger, server_default="0")
    duplicates: Mapped[int] = mapped_column(BigInteger, server_default="0")
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)

    marketplace: Mapped[Marketplace | None] = relationship()


class JobItem(Base):
    __tablename__ = "extraction_job_items"
    id: Mapped[int] = _bigint_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_jobs.id"))
    external_product_id: Mapped[str] = mapped_column(Text)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("marketplace_listings.id"))
    item_status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = _bigint_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_jobs.id"))
    event_type: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(Text, server_default="info")
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    created_at: Mapped[datetime] = _created()


class Observation(Base):
    __tablename__ = "listing_observations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger)
    price: Mapped[Decimal | None] = mapped_column(Money)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    shipping_price: Mapped[Decimal | None] = mapped_column(Money)
    stock_status: Mapped[str | None] = mapped_column(Text)
    stock_quantity: Mapped[int | None] = mapped_column(Integer)
    sales_rank: Mapped[int | None] = mapped_column(BigInteger)
    rank_category: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    review_count: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_reference: Mapped[str | None] = mapped_column(Text)


class ProductMetric(Base):
    __tablename__ = "product_metrics"
    id: Mapped[int] = _bigint_pk()
    listing_id: Mapped[int] = mapped_column(ForeignKey("marketplace_listings.id"))
    metric_name: Mapped[str] = mapped_column(Text)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit: Mapped[str | None] = mapped_column(Text)
    measurement_type: Mapped[str] = mapped_column(Text)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    methodology: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    source: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = _created()


class FeeProfile(Base):
    __tablename__ = "fee_profiles"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organisations.id"))
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    name: Mapped[str] = mapped_column(Text)
    category_pattern: Mapped[str | None] = mapped_column(Text)
    referral_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), server_default="0")
    fixed_fee: Mapped[Decimal] = mapped_column(Money, server_default="0")
    payment_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), server_default="0")
    payment_fixed_fee: Mapped[Decimal] = mapped_column(Money, server_default="0")
    fulfilment_fee: Mapped[Decimal] = mapped_column(Money, server_default="0")
    currency_code: Mapped[str] = mapped_column(String(3))
    notes: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[date] = mapped_column(Date, server_default=text("CURRENT_DATE"))
    created_at: Mapped[datetime] = _created()


class ProductCost(Base):
    __tablename__ = "product_costs"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("marketplace_listings.id"))
    canonical_product_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_products.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    currency_code: Mapped[str] = mapped_column(String(3))
    supplier_cost: Mapped[Decimal] = mapped_column(Money, server_default="0")
    shipping_cost: Mapped[Decimal] = mapped_column(Money, server_default="0")
    marketplace_fees: Mapped[Decimal | None] = mapped_column(Money)
    advertising_cost: Mapped[Decimal] = mapped_column(Money, server_default="0")
    packaging_cost: Mapped[Decimal] = mapped_column(Money, server_default="0")
    returns_allowance_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), server_default="0")
    other_costs: Mapped[Decimal] = mapped_column(Money, server_default="0")
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    tax_included_in_price: Mapped[bool | None] = mapped_column(Boolean)
    fee_profile_id: Mapped[int | None] = mapped_column(ForeignKey("fee_profiles.id"))
    assessment: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    notes: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[datetime] = _created()
    created_at: Mapped[datetime] = _created()


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    id: Mapped[int] = _bigint_pk()
    source: Mapped[str] = mapped_column(Text)
    base_currency: Mapped[str] = mapped_column(String(3))
    quote_currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extraction_jobs.id"))
    export_format: Mapped[str] = mapped_column(Text)
    export_status: Mapped[str] = mapped_column(Text, server_default="pending")
    filters: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    sheets: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    includes_own_account_data: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    storage_key: Mapped[str | None] = mapped_column(Text)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0")
    error_summary: Mapped[str | None] = mapped_column(Text)
    manifest: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_projects.id"))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("marketplace_listings.id"))
    rule_type: Mapped[str] = mapped_column(Text)
    threshold: Mapped[Decimal | None] = mapped_column(Money)
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = _created()


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = _bigint_pk()
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organisations.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    created_at: Mapped[datetime] = _created()


class ConnectorHealth(Base):
    __tablename__ = "connector_health"
    connector: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, server_default="unknown")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    rate_limited_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _created()
