"""The common connector contract.

Every marketplace integration implements :class:`MarketplaceConnector`, publishes a
:class:`CapabilityManifest` describing exactly what it can and cannot provide, and
returns typed, normalised records. Values a source does not expose are ``None`` (or a
metric with ``measurement_type='unavailable'``) - never zero or a guess.
"""

from __future__ import annotations

import abc
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    OWN_ACCOUNT = "own_account"
    COMPETITOR_RESEARCH = "competitor_research"
    PARTNER_ESTIMATE = "partner_estimate"
    USER_ENTERED = "user_entered"
    DERIVED = "derived"


class MeasurementType(StrEnum):
    ACTUAL = "actual"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class StockStatus(StrEnum):
    IN_STOCK = "in_stock"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    UNKNOWN = "unknown"


class InputType(StrEnum):
    SHOP_URL = "shop_url"
    CATEGORY_URL = "category_url"
    PRODUCT_URL = "product_url"
    KEYWORD = "keyword"
    CATEGORY = "category"
    LISTING_IDS = "listing_ids"


class ReleaseStatus(StrEnum):
    STABLE = "stable"
    BETA = "beta"
    SANDBOX = "sandbox"
    PLANNED = "planned"


# ----------------------------------------------------------------- errors
class ConnectorError(Exception):
    code = "connector_error"
    retryable = False

    def __init__(self, message: str, *, retryable: bool | None = None):
        super().__init__(message)
        self.message = message
        if retryable is not None:
            self.retryable = retryable


class TransientError(ConnectorError):
    code = "transient"
    retryable = True


class RateLimited(TransientError):
    code = "rate_limited"

    def __init__(self, message: str = "Rate limited by source", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class AuthenticationFailed(ConnectorError):
    code = "auth_failed"


class PermissionDenied(ConnectorError):
    code = "permission_denied"


class ProductUnavailable(ConnectorError):
    code = "unavailable"


class NotConfigured(ConnectorError):
    code = "not_configured"


class UnsupportedInput(ConnectorError):
    code = "unsupported_input"


# ----------------------------------------------------------------- models
class RateLimitPolicy(BaseModel):
    requests_per_second: float
    burst: int = 1
    max_concurrency: int = 2
    honours_retry_after: bool = True


class CapabilityManifest(BaseModel):
    key: str
    platform: str
    display_name: str
    version: str
    release_status: ReleaseStatus
    marketplaces: list[str]
    url_hosts: list[str] = Field(default_factory=list)
    supported_inputs: list[InputType]
    own_account_streams: list[str] = Field(default_factory=list)
    auth_type: str
    required_scopes: list[str] = Field(default_factory=list)
    fields_available: list[str]
    known_missing_fields: list[str] = Field(default_factory=list)
    metric_provenance: dict[str, MeasurementType] = Field(default_factory=dict)
    pagination_style: str
    max_results_per_query: int | None = None
    rate_limit: RateLimitPolicy
    data_licence_note: str
    limitations: list[str] = Field(default_factory=list)
    sandbox_supported: bool = False
    configured: bool = False
    configuration_hint: str | None = None


class ConnectorInput(BaseModel):
    input_type: InputType
    marketplace_code: str
    url: str | None = None
    keyword: str | None = None
    category: str | None = None
    listing_ids: list[str] | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class ValidatedInput(BaseModel):
    input_type: InputType
    marketplace_code: str
    normalised_url: str | None = None
    keyword: str | None = None
    seller_id: str | None = None
    category_id: str | None = None
    product_id: str | None = None
    listing_ids: list[str] | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    source_limit_note: str | None = None


class DiscoveryPage(BaseModel):
    external_ids: list[str]
    next_cursor: str | None = None
    total_estimate: int | None = None
    source_limit_reached: bool = False
    note: str | None = None


class MetricValue(BaseModel):
    name: str
    value: Decimal | None = None
    unit: str | None = None
    measurement_type: MeasurementType
    methodology: str | None = None
    confidence: Decimal | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    source: str

    def model_post_init(self, __context: Any) -> None:
        if self.measurement_type == MeasurementType.UNAVAILABLE:
            self.value = None
        if self.measurement_type == MeasurementType.ESTIMATED and not self.methodology:
            raise ValueError(f"Estimated metric {self.name!r} must declare its methodology")


class NormalisedImage(BaseModel):
    url: str
    image_type: str | None = None
    width: int | None = None
    height: int | None = None
    variation_external_id: str | None = None


class NormalisedVariation(BaseModel):
    external_variation_id: str
    variation_sku: str | None = None
    size: str | None = None
    colour: str | None = None
    price: Decimal | None = None
    currency_code: str | None = None
    stock_status: StockStatus | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class Availability(BaseModel):
    stock_status: StockStatus = StockStatus.UNKNOWN
    quantity: int | None = None
    quantity_type: MeasurementType = MeasurementType.UNAVAILABLE
    price: Decimal | None = None
    currency_code: str | None = None
    shipping_price: Decimal | None = None


class NormalisedListing(BaseModel):
    external_listing_id: str
    marketplace_code: str
    title: str
    product_url: str
    source_type: SourceType = SourceType.COMPETITOR_RESEARCH
    parent_listing_id: str | None = None
    seller_sku: str | None = None
    asin: str | None = None
    gtin: str | None = None
    mpn: str | None = None
    description: str | None = None
    brand: str | None = None
    category_path: str | None = None
    category_id: str | None = None
    currency_code: str | None = None
    current_price: Decimal | None = None
    original_price: Decimal | None = None
    shipping_price: Decimal | None = None
    stock_status: StockStatus = StockStatus.UNKNOWN
    stock_quantity: int | None = None
    stock_quantity_type: MeasurementType = MeasurementType.UNAVAILABLE
    condition: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    sales_rank: int | None = None
    rank_category: str | None = None
    seller_external_id: str | None = None
    seller_name: str | None = None
    fulfilment_type: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    images: list[NormalisedImage] = Field(default_factory=list)
    variations: list[NormalisedVariation] = Field(default_factory=list)
    metrics: list[MetricValue] = Field(default_factory=list)
    endpoint: str | None = None
    source_reference: str | None = None
    retrieved_at: datetime | None = None


class OrderItemRecord(BaseModel):
    external_order_item_id: str
    external_listing_id: str | None = None
    seller_sku: str | None = None
    quantity: int = 1
    unit_price: Decimal | None = None
    item_tax: Decimal | None = None
    marketplace_fees: Decimal | None = None
    refunded_amount: Decimal = Decimal("0")
    currency_code: str | None = None


class OrderRecord(BaseModel):
    external_order_id: str
    order_status: str | None = None
    order_currency: str | None = None
    order_total: Decimal | None = None
    ordered_at: datetime | None = None
    source_updated_at: datetime | None = None
    items: list[OrderItemRecord] = Field(default_factory=list)


class OrderPage(BaseModel):
    orders: list[OrderRecord]
    next_cursor: str | None = None


class AccountCredentials(BaseModel):
    """Decrypted credential payload handed to a connector for one call; never persisted."""

    refresh_token: str | None = None
    access_token: str | None = None
    access_token_expires_at: datetime | None = None
    external_account_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class OAuthStart(BaseModel):
    authorization_url: str
    state: str


class OAuthResult(BaseModel):
    credentials: AccountCredentials
    external_account_id: str | None = None
    granted_scopes: list[str] = Field(default_factory=list)


# ------------------------------------------------------------- contract
class MarketplaceConnector(abc.ABC):
    """Each connector owns one platform and may serve several regional marketplaces."""

    manifest: CapabilityManifest

    @abc.abstractmethod
    def is_configured(self) -> bool: ...

    def describe(self) -> CapabilityManifest:
        return self.manifest.model_copy(update={"configured": self.is_configured()})

    @abc.abstractmethod
    async def validate_input(self, data: ConnectorInput) -> ValidatedInput: ...

    @abc.abstractmethod
    async def discover_products(self, data: ValidatedInput, cursor: str | None = None) -> DiscoveryPage: ...

    @abc.abstractmethod
    async def get_product_details(self, marketplace_code: str, external_product_id: str) -> NormalisedListing: ...

    async def get_variations(self, marketplace_code: str, external_product_id: str) -> list[NormalisedVariation]:
        return (await self.get_product_details(marketplace_code, external_product_id)).variations

    async def get_availability(self, marketplace_code: str, external_product_id: str) -> Availability:
        listing = await self.get_product_details(marketplace_code, external_product_id)
        return Availability(
            stock_status=listing.stock_status,
            quantity=listing.stock_quantity,
            quantity_type=listing.stock_quantity_type,
            price=listing.current_price,
            currency_code=listing.currency_code,
            shipping_price=listing.shipping_price,
        )

    async def health_check(self) -> bool:
        return self.is_configured()

    # ---- own-account (optional) ------------------------------------
    def start_oauth(self, marketplace_code: str, state: str, redirect_uri: str) -> OAuthStart:
        raise UnsupportedInput(f"{self.manifest.display_name} does not support account connections yet")

    async def complete_oauth(self, marketplace_code: str, params: dict[str, str], redirect_uri: str) -> OAuthResult:
        raise UnsupportedInput(f"{self.manifest.display_name} does not support account connections yet")

    async def list_orders(
        self, marketplace_code: str, credentials: AccountCredentials, updated_since: datetime, cursor: str | None
    ) -> OrderPage:
        raise UnsupportedInput(f"{self.manifest.display_name} does not support order sync")

    async def list_account_listings(
        self, marketplace_code: str, credentials: AccountCredentials, cursor: str | None
    ) -> tuple[list[NormalisedListing], str | None]:
        raise UnsupportedInput(f"{self.manifest.display_name} does not support catalogue sync")

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
