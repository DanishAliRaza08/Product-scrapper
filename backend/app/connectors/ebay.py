"""eBay connector using the official Buy Browse API (research) and Sell Fulfillment
API (own-account orders). Requires an eBay developer application.

Docs: https://developer.ebay.com/api-docs/buy/browse/overview.html
"""

from __future__ import annotations

import base64
import re
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from app.connectors.base import (
    AccountCredentials,
    AuthenticationFailed,
    CapabilityManifest,
    ConnectorError,
    ConnectorInput,
    DiscoveryPage,
    InputType,
    MarketplaceConnector,
    MeasurementType,
    MetricValue,
    NormalisedImage,
    NormalisedListing,
    NormalisedVariation,
    NotConfigured,
    OAuthResult,
    OAuthStart,
    OrderItemRecord,
    OrderPage,
    OrderRecord,
    ProductUnavailable,
    RateLimitPolicy,
    ReleaseStatus,
    StockStatus,
    UnsupportedInput,
    ValidatedInput,
)
from app.connectors.http import ResilientClient
from app.core.config import get_settings
from app.core.ssrf import host_matches, normalise_url

MARKETPLACE_IDS = {"EBAY_US": "EBAY_US", "EBAY_UK": "EBAY_GB"}
HOSTS = {"ebay.com": "EBAY_US", "ebay.co.uk": "EBAY_UK"}
PAGE_LIMIT = 200
MAX_OFFSET = 10_000  # Browse API: offset + limit must not exceed 10,000
APP_SCOPE = "https://api.ebay.com/oauth/api_scope"
USER_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly",
]
STOCK_MAP = {
    "IN_STOCK": StockStatus.IN_STOCK,
    "LIMITED_STOCK": StockStatus.LOW_STOCK,
    "OUT_OF_STOCK": StockStatus.OUT_OF_STOCK,
}


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class EbayConnector(MarketplaceConnector):
    manifest = CapabilityManifest(
        key="ebay",
        platform="ebay",
        display_name="eBay (Browse API + Sell APIs)",
        version="1.0.0",
        release_status=ReleaseStatus.BETA,
        marketplaces=["EBAY_US", "EBAY_UK"],
        url_hosts=list(HOSTS),
        supported_inputs=[
            InputType.SHOP_URL,
            InputType.CATEGORY_URL,
            InputType.PRODUCT_URL,
            InputType.KEYWORD,
            InputType.CATEGORY,
            InputType.LISTING_IDS,
        ],
        own_account_streams=["orders"],
        auth_type="OAuth 2.0 (client credentials for research, authorisation code for seller accounts)",
        required_scopes=USER_SCOPES,
        fields_available=[
            "title",
            "description",
            "brand",
            "gtin",
            "mpn",
            "category",
            "price",
            "shipping",
            "images",
            "variations (item groups)",
            "condition",
            "seller",
            "stock_status",
            "estimated_sold_quantity",
            "product rating where catalogued",
        ],
        known_missing_fields=["sales rank", "exact competitor sales", "exact stock quantity for most listings"],
        metric_provenance={
            "estimated_sold_quantity": MeasurementType.ESTIMATED,
            "estimated_available_quantity": MeasurementType.ESTIMATED,
            "competitor_sales": MeasurementType.UNAVAILABLE,
        },
        pagination_style="offset/limit",
        max_results_per_query=MAX_OFFSET,
        rate_limit=RateLimitPolicy(requests_per_second=4, burst=8, max_concurrency=4),
        data_licence_note="Subject to the eBay API License Agreement; cache and display rules apply.",
        limitations=[
            "Active listings only; completed transactions are not exposed by the Browse API.",
            "Shop extraction requires a keyword or category in addition to the seller (Browse API rule).",
            "At most 10,000 results per query.",
        ],
        sandbox_supported=True,
        configuration_hint="Set MPI_EBAY_CLIENT_ID, MPI_EBAY_CLIENT_SECRET and MPI_EBAY_RU_NAME.",
    )

    def __init__(self, transport=None):
        settings = get_settings()
        sandbox = settings.ebay_environment == "sandbox"
        self.api_base = "https://api.sandbox.ebay.com" if sandbox else "https://api.ebay.com"
        self.auth_base = "https://auth.sandbox.ebay.com" if sandbox else "https://auth.ebay.com"
        self.http = ResilientClient("ebay", self.manifest.rate_limit, base_url=self.api_base, transport=transport)
        self._app_token: tuple[str, float] | None = None

    def is_configured(self) -> bool:
        s = get_settings()
        return bool(s.ebay_client_id and s.ebay_client_secret)

    def _basic_auth(self) -> str:
        s = get_settings()
        if not self.is_configured():
            raise NotConfigured(self.manifest.configuration_hint or "eBay is not configured")
        raw = f"{s.ebay_client_id}:{s.ebay_client_secret.get_secret_value()}"  # type: ignore[union-attr]
        return "Basic " + base64.b64encode(raw.encode()).decode()

    async def _token_request(self, form: dict[str, str]) -> dict:
        return await self.http.post_form(
            "/identity/v1/oauth2/token", form, headers={"Authorization": self._basic_auth()}
        )

    async def _application_token(self) -> str:
        if self._app_token and self._app_token[1] > time.time() + 60:
            return self._app_token[0]
        data = await self._token_request({"grant_type": "client_credentials", "scope": APP_SCOPE})
        self._app_token = (data["access_token"], time.time() + int(data.get("expires_in", 7200)))
        return self._app_token[0]

    async def _browse_headers(self, marketplace_code: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._application_token()}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_IDS[marketplace_code],
        }

    # ------------------------------------------------------------ input
    async def validate_input(self, data: ConnectorInput) -> ValidatedInput:
        if data.marketplace_code not in MARKETPLACE_IDS:
            raise UnsupportedInput(f"{data.marketplace_code} is not an eBay marketplace")
        out = ValidatedInput(input_type=data.input_type, marketplace_code=data.marketplace_code, filters=data.filters)
        if data.input_type in (InputType.SHOP_URL, InputType.CATEGORY_URL, InputType.PRODUCT_URL):
            if not data.url:
                raise UnsupportedInput("A URL is required for this input type")
            url = normalise_url(data.url)
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if not host_matches(host, tuple(HOSTS)):
                raise UnsupportedInput("URL must be an ebay.com or ebay.co.uk address")
            host_market = next(m for h, m in HOSTS.items() if host_matches(host, (h,)))
            if host_market != data.marketplace_code:
                out.warnings.append(f"URL belongs to {host_market}; results use {data.marketplace_code} pricing.")
            out.normalised_url = url
            query = parse_qs(parts.query)
            if data.input_type == InputType.PRODUCT_URL:
                m = re.search(r"/itm/(?:[^/]+/)?(\d{9,15})", parts.path)
                if not m:
                    raise UnsupportedInput("Could not find an eBay item number in the URL")
                out.product_id = m.group(1)
            elif data.input_type == InputType.SHOP_URL:
                m = re.search(r"/(?:str|usr)/([^/?#]+)", parts.path)
                seller = m.group(1) if m else (query.get("_ssn") or [None])[0]
                if not seller:
                    raise UnsupportedInput("Could not find a seller or store name in the URL")
                out.seller_id = seller
                out.keyword = (query.get("_nkw") or [data.keyword or None])[0]
                out.category_id = (query.get("_sacat") or [data.category or None])[0]
                if not out.keyword and not out.category_id:
                    raise UnsupportedInput(
                        "eBay's Browse API needs a keyword or category alongside a seller. "
                        "Add a keyword (e.g. the shop's main product type) and retry."
                    )
            else:
                m = re.search(r"/b/[^/]+/(\d+)", parts.path)
                category = m.group(1) if m else (query.get("_sacat") or [None])[0]
                if not category or not category.isdigit():
                    raise UnsupportedInput("Could not find an eBay category ID in the URL")
                out.category_id = category
                out.keyword = (query.get("_nkw") or [None])[0]
        elif data.input_type == InputType.KEYWORD:
            if not (data.keyword or "").strip():
                raise UnsupportedInput("Keyword is required")
            out.keyword = data.keyword.strip()[:350]  # type: ignore[union-attr]
            out.category_id = data.category
        elif data.input_type == InputType.CATEGORY:
            if not (data.category or "").isdigit():
                raise UnsupportedInput("eBay category must be a numeric category ID")
            out.category_id = data.category
        elif data.input_type == InputType.LISTING_IDS:
            out.listing_ids = [i for i in (data.listing_ids or []) if re.fullmatch(r"\d{9,15}|v1\|\d+\|\d+", i)]
            if not out.listing_ids:
                raise UnsupportedInput("Provide eBay item numbers")
        out.source_limit_note = "eBay Browse API returns at most 10,000 active listings per query."
        return out

    # -------------------------------------------------------- discovery
    async def discover_products(self, data: ValidatedInput, cursor: str | None = None) -> DiscoveryPage:
        if data.product_id:
            return DiscoveryPage(external_ids=[data.product_id], total_estimate=1)
        if data.listing_ids:
            return DiscoveryPage(external_ids=data.listing_ids, total_estimate=len(data.listing_ids))
        offset = int(cursor or 0)
        params: dict[str, Any] = {"limit": PAGE_LIMIT, "offset": offset}
        if data.keyword:
            params["q"] = data.keyword
        if data.category_id:
            params["category_ids"] = data.category_id
        filters = []
        if data.seller_id:
            filters.append(f"sellers:{{{data.seller_id}}}")
        if (price := data.filters.get("price_min")) is not None or data.filters.get("price_max") is not None:
            lo, hi = price or "", data.filters.get("price_max") or ""
            currency = "GBP" if data.marketplace_code == "EBAY_UK" else "USD"
            filters += [f"price:[{lo}..{hi}]", f"priceCurrency:{currency}"]
        if data.filters.get("condition") == "new":
            filters.append("conditions:{NEW}")
        if filters:
            params["filter"] = ",".join(filters)
        payload = await self.http.get_json(
            "/buy/browse/v1/item_summary/search",
            params=params,
            headers=await self._browse_headers(data.marketplace_code),
        )
        summaries = payload.get("itemSummaries") or []
        total = int(payload.get("total") or 0)
        next_offset = offset + PAGE_LIMIT
        more = bool(payload.get("next")) and next_offset < MAX_OFFSET
        limited = total > MAX_OFFSET and not more
        return DiscoveryPage(
            external_ids=[s.get("legacyItemId") or s["itemId"] for s in summaries],
            next_cursor=str(next_offset) if more else None,
            total_estimate=total,
            source_limit_reached=limited,
            note="eBay returns at most 10,000 results for one query; narrow with categories or price bands."
            if limited
            else None,
        )

    # ---------------------------------------------------------- details
    async def _get_item(self, marketplace_code: str, external_id: str) -> dict:
        headers = await self._browse_headers(marketplace_code)
        if external_id.startswith("v1|"):
            return await self.http.get_json(f"/buy/browse/v1/item/{quote(external_id, safe='')}", headers=headers)
        return await self.http.get_json(
            "/buy/browse/v1/item/get_item_by_legacy_id", params={"legacy_item_id": external_id}, headers=headers
        )

    async def get_product_details(self, marketplace_code: str, external_product_id: str) -> NormalisedListing:
        try:
            item = await self._get_item(marketplace_code, external_product_id)
            return self._map_item(marketplace_code, external_product_id, item, [])
        except ConnectorError as exc:
            # Multi-variation listings are rejected (HTTP 400) by get_item_by_legacy_id and
            # must be fetched through the item-group endpoint instead.
            if type(exc) is not ConnectorError or not external_product_id.isdigit():
                raise
        group = await self.http.get_json(
            "/buy/browse/v1/item/get_items_by_item_group",
            params={"item_group_id": external_product_id},
            headers=await self._browse_headers(marketplace_code),
        )
        group_items = group.get("items") or []
        if not group_items:
            raise ProductUnavailable("eBay returned no items for this listing")
        return self._map_item(marketplace_code, external_product_id, group_items[0], group_items)

    def _map_item(
        self, marketplace_code: str, external_id: str, item: dict, group_items: list[dict]
    ) -> NormalisedListing:
        price = item.get("price") or {}
        avail = (item.get("estimatedAvailabilities") or [{}])[0]
        shipping = (item.get("shippingOptions") or [{}])[0].get("shippingCost") or {}
        rating = (item.get("product") or {}).get("reviewRating") or item.get("reviewRating") or {}
        images = []
        if item.get("image", {}).get("imageUrl"):
            images.append(NormalisedImage(url=item["image"]["imageUrl"], image_type="main"))
        images += [NormalisedImage(url=i["imageUrl"], image_type="gallery") for i in item.get("additionalImages") or []]
        variations = []
        for v in group_items:
            aspects = {a.get("name", "").lower(): a.get("value") for a in v.get("localizedAspects") or []}
            v_avail = (v.get("estimatedAvailabilities") or [{}])[0]
            variations.append(
                NormalisedVariation(
                    external_variation_id=v.get("itemId", ""),
                    size=aspects.get("size"),
                    colour=aspects.get("colour") or aspects.get("color"),
                    price=_dec((v.get("price") or {}).get("value")),
                    currency_code=(v.get("price") or {}).get("currency"),
                    stock_status=STOCK_MAP.get(v_avail.get("estimatedAvailabilityStatus"), StockStatus.UNKNOWN),
                    attributes=aspects,
                )
            )
        now = datetime.now(UTC)
        metrics = [
            MetricValue(name="competitor_sales", measurement_type=MeasurementType.UNAVAILABLE, source="ebay:browse")
        ]
        if avail.get("estimatedSoldQuantity") is not None:
            metrics.append(
                MetricValue(
                    name="estimated_sold_quantity",
                    value=_dec(avail["estimatedSoldQuantity"]),
                    unit="units (listing lifetime)",
                    measurement_type=MeasurementType.ESTIMATED,
                    methodology="eBay Browse API estimatedSoldQuantity (eBay-provided estimate)",
                    source="ebay:browse",
                )
            )
        qty = avail.get("estimatedAvailableQuantity")
        return NormalisedListing(
            external_listing_id=external_id,
            marketplace_code=marketplace_code,
            title=item.get("title") or "(untitled)",
            product_url=item.get("itemWebUrl") or f"https://www.ebay.com/itm/{external_id}",
            parent_listing_id=item.get("primaryItemGroup", {}).get("itemGroupId"),
            gtin=item.get("gtin"),
            mpn=item.get("mpn"),
            description=item.get("shortDescription") or item.get("description"),
            brand=item.get("brand"),
            category_path=(item.get("categoryPath") or "").replace("|", " > ") or None,
            category_id=item.get("categoryId"),
            currency_code=price.get("currency"),
            current_price=_dec(price.get("value")),
            original_price=_dec(((item.get("marketingPrice") or {}).get("originalPrice") or {}).get("value")),
            shipping_price=_dec(shipping.get("value")),
            stock_status=STOCK_MAP.get(avail.get("estimatedAvailabilityStatus"), StockStatus.UNKNOWN),
            stock_quantity=int(qty) if qty is not None else None,
            stock_quantity_type=MeasurementType.ESTIMATED if qty is not None else MeasurementType.UNAVAILABLE,
            condition=item.get("condition"),
            rating=_dec(rating.get("averageRating")),
            review_count=rating.get("reviewCount"),
            seller_external_id=(item.get("seller") or {}).get("username"),
            seller_name=(item.get("seller") or {}).get("username"),
            attributes={a.get("name"): a.get("value") for a in item.get("localizedAspects") or []},
            images=images,
            variations=variations,
            metrics=metrics,
            endpoint="buy/browse/v1/item",
            source_reference=item.get("itemId"),
            retrieved_at=now,
        )

    # ---------------------------------------------------- own account
    def start_oauth(self, marketplace_code: str, state: str, redirect_uri: str) -> OAuthStart:
        s = get_settings()
        if not (self.is_configured() and s.ebay_ru_name):
            raise NotConfigured(self.manifest.configuration_hint or "eBay is not configured")
        query = urlencode(
            {
                "client_id": s.ebay_client_id,
                "response_type": "code",
                "redirect_uri": s.ebay_ru_name,  # eBay uses the RuName, not the raw URL
                "scope": " ".join(USER_SCOPES),
                "state": state,
            }
        )
        return OAuthStart(authorization_url=f"{self.auth_base}/oauth2/authorize?{query}", state=state)

    async def complete_oauth(self, marketplace_code: str, params: dict[str, str], redirect_uri: str) -> OAuthResult:
        code = params.get("code")
        if not code:
            raise AuthenticationFailed("eBay did not return an authorisation code")
        data = await self._token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": get_settings().ebay_ru_name or ""}
        )
        return OAuthResult(
            credentials=AccountCredentials(
                refresh_token=data.get("refresh_token"),
                access_token=data.get("access_token"),
                access_token_expires_at=datetime.fromtimestamp(time.time() + int(data.get("expires_in", 7200)), UTC),
            ),
            granted_scopes=USER_SCOPES,
        )

    async def _user_token(self, credentials: AccountCredentials) -> str:
        if (
            credentials.access_token
            and credentials.access_token_expires_at
            and credentials.access_token_expires_at.timestamp() > time.time() + 60
        ):
            return credentials.access_token
        if not credentials.refresh_token:
            raise AuthenticationFailed("Seller authorisation has expired; reconnect the account")
        data = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": credentials.refresh_token, "scope": " ".join(USER_SCOPES)}
        )
        credentials.access_token = data["access_token"]
        credentials.access_token_expires_at = datetime.fromtimestamp(
            time.time() + int(data.get("expires_in", 7200)), UTC
        )
        return credentials.access_token

    async def list_orders(
        self, marketplace_code: str, credentials: AccountCredentials, updated_since: datetime, cursor: str | None
    ) -> OrderPage:
        token = await self._user_token(credentials)
        offset = int(cursor or 0)
        since = updated_since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        payload = await self.http.get_json(
            "/sell/fulfillment/v1/order",
            params={"filter": f"lastmodifieddate:[{since}..]", "limit": 200, "offset": offset},
            headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_IDS[marketplace_code]},
        )
        orders = []
        for o in payload.get("orders") or []:
            total = (o.get("pricingSummary") or {}).get("total") or {}
            items = []
            for li in o.get("lineItems") or []:
                cost = li.get("lineItemCost") or {}
                qty = int(li.get("quantity") or 1)
                unit = _dec(cost.get("value"))
                refunds = sum(
                    (_dec((r.get("amount") or {}).get("value")) or Decimal(0)) for r in li.get("refunds") or []
                )
                items.append(
                    OrderItemRecord(
                        external_order_item_id=li.get("lineItemId"),
                        external_listing_id=li.get("legacyItemId"),
                        seller_sku=li.get("sku"),
                        quantity=qty,
                        unit_price=(unit / qty) if unit is not None and qty else unit,
                        currency_code=cost.get("currency"),
                        refunded_amount=refunds,
                    )
                )
            orders.append(
                OrderRecord(
                    external_order_id=o["orderId"],
                    order_status=o.get("orderFulfillmentStatus"),
                    order_currency=total.get("currency"),
                    order_total=_dec(total.get("value")),
                    ordered_at=_dt(o.get("creationDate")),
                    source_updated_at=_dt(o.get("lastModifiedDate")),
                    items=items,
                )
            )
        more = payload.get("next") is not None
        return OrderPage(orders=orders, next_cursor=str(offset + 200) if more else None)

    async def aclose(self) -> None:
        await self.http.aclose()
