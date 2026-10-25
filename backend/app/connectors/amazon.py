"""Amazon connector using the Selling Partner API (SP-API).

Research uses the Catalog Items API (2022-04-01) and Product Pricing API with the
organisation's own authorised SP-API application. Own-account sync uses the Orders API.
SP-API does not expose competitor storefront catalogues or competitor sales.

Docs: https://developer-docs.amazon.com/sp-api/
"""

from __future__ import annotations

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
    RateLimitPolicy,
    ReleaseStatus,
    StockStatus,
    UnsupportedInput,
    ValidatedInput,
)
from app.connectors.http import ResilientClient
from app.core.config import get_settings
from app.core.ssrf import host_matches, normalise_url

REGIONS = {
    "AMAZON_US": ("https://sellingpartnerapi-na.amazon.com", "ATVPDKIKX0DER", "https://sellercentral.amazon.com"),
    "AMAZON_UK": ("https://sellingpartnerapi-eu.amazon.com", "A1F83G8C2ARO7P", "https://sellercentral.amazon.co.uk"),
}
HOSTS = {"amazon.com": "AMAZON_US", "amazon.co.uk": "AMAZON_UK"}
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"  # noqa: S105
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?]|$)")
CATALOG_INCLUDED = "attributes,identifiers,images,productTypes,relationships,salesRanks,summaries"
PAGE_SIZE = 20


def _dec(value: Any) -> Decimal | None:
    try:
        return None if value in (None, "") else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _first_attr(attributes: dict, name: str) -> Any:
    values = attributes.get(name) or []
    return values[0].get("value") if values and isinstance(values[0], dict) else None


class AmazonConnector(MarketplaceConnector):
    manifest = CapabilityManifest(
        key="amazon",
        platform="amazon",
        display_name="Amazon (Selling Partner API)",
        version="1.0.0",
        release_status=ReleaseStatus.BETA,
        marketplaces=list(REGIONS),
        url_hosts=list(HOSTS),
        supported_inputs=[
            InputType.PRODUCT_URL,
            InputType.KEYWORD,
            InputType.CATEGORY_URL,
            InputType.CATEGORY,
            InputType.LISTING_IDS,
        ],
        own_account_streams=["orders"],
        auth_type="Login with Amazon (LWA) refresh token per authorised selling partner",
        required_scopes=["Product Listing", "Pricing", "Inventory and Order Tracking"],
        fields_available=[
            "title",
            "brand",
            "identifiers (EAN/UPC/GTIN)",
            "images",
            "category/classification",
            "sales rank",
            "variation relationships",
            "competitive offers & buy box price",
            "attributes",
        ],
        known_missing_fields=[
            "competitor storefront catalogues",
            "competitor sales",
            "competitor stock quantity",
            "reviews text",
        ],
        metric_provenance={
            "sales_rank": MeasurementType.ACTUAL,
            "offer_count": MeasurementType.ACTUAL,
            "competitor_sales": MeasurementType.UNAVAILABLE,
        },
        pagination_style="pageToken",
        max_results_per_query=1000,
        rate_limit=RateLimitPolicy(requests_per_second=1.5, burst=2, max_concurrency=2),
        data_licence_note="Subject to the Amazon Services API Developer Agreement and Data Protection Policy.",
        limitations=[
            "Competitor shop URLs are not supported: SP-API does not list another seller's storefront.",
            "Order PII is not requested; only non-restricted order fields are synced.",
            "Catalog search returns a bounded number of results per query.",
        ],
        configuration_hint=(
            "Set MPI_AMAZON_LWA_CLIENT_ID, MPI_AMAZON_LWA_CLIENT_SECRET, MPI_AMAZON_SPAPI_APP_ID and, "
            "for research, MPI_AMAZON_RESEARCH_REFRESH_TOKEN."
        ),
    )

    def __init__(self, transport=None):
        self.transport = transport
        self.lwa = ResilientClient("amazon-lwa", RateLimitPolicy(requests_per_second=5, burst=5), transport=transport)
        self.clients: dict[str, ResilientClient] = {}
        self._research_token: tuple[str, float] | None = None

    def is_configured(self) -> bool:
        s = get_settings()
        return bool(s.amazon_lwa_client_id and s.amazon_lwa_client_secret)

    def _client(self, marketplace_code: str) -> ResilientClient:
        if marketplace_code not in self.clients:
            self.clients[marketplace_code] = ResilientClient(
                f"amazon-{marketplace_code}",
                self.manifest.rate_limit,
                base_url=REGIONS[marketplace_code][0],
                transport=self.transport,
            )
        return self.clients[marketplace_code]

    async def _lwa(self, form: dict[str, str]) -> dict:
        s = get_settings()
        if not self.is_configured():
            raise NotConfigured(self.manifest.configuration_hint or "Amazon is not configured")
        form = form | {
            "client_id": s.amazon_lwa_client_id or "",
            "client_secret": s.amazon_lwa_client_secret.get_secret_value(),  # type: ignore[union-attr]
        }
        return await self.lwa.post_form(LWA_TOKEN_URL, form)

    async def _access_token(self, refresh_token: str | None = None) -> str:
        if refresh_token is None:
            if self._research_token and self._research_token[1] > time.time() + 60:
                return self._research_token[0]
            secret = get_settings().amazon_research_refresh_token
            if secret is None:
                raise NotConfigured("Research requires MPI_AMAZON_RESEARCH_REFRESH_TOKEN from an authorised seller")
            data = await self._lwa({"grant_type": "refresh_token", "refresh_token": secret.get_secret_value()})
            self._research_token = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
            return data["access_token"]
        data = await self._lwa({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return data["access_token"]

    async def _get(self, marketplace_code: str, path: str, params: dict, token: str | None = None) -> dict:
        token = token or await self._access_token()
        return await self._client(marketplace_code).get_json(path, params=params, headers={"x-amz-access-token": token})

    # ------------------------------------------------------------ input
    async def validate_input(self, data: ConnectorInput) -> ValidatedInput:
        if data.marketplace_code not in REGIONS:
            raise UnsupportedInput(f"{data.marketplace_code} is not an Amazon marketplace")
        if data.input_type == InputType.SHOP_URL:
            raise UnsupportedInput(
                "Amazon does not provide an API for listing a competitor's storefront. "
                "Use keyword or category research, or connect your own seller account."
            )
        out = ValidatedInput(input_type=data.input_type, marketplace_code=data.marketplace_code, filters=data.filters)
        if data.input_type in (InputType.PRODUCT_URL, InputType.CATEGORY_URL):
            url = normalise_url(data.url or "")
            parts = urlsplit(url)
            if not host_matches(parts.hostname or "", tuple(HOSTS)):
                raise UnsupportedInput("URL must be an amazon.com or amazon.co.uk address")
            out.normalised_url = url
            if data.input_type == InputType.PRODUCT_URL:
                m = ASIN_RE.search(parts.path + "/")
                if not m:
                    raise UnsupportedInput("Could not find an ASIN in the URL")
                out.product_id = m.group(1)
            else:
                q = parse_qs(parts.query)
                node = (q.get("node") or [None])[0]
                if not node:
                    rh = (q.get("rh") or [""])[0]
                    m = re.search(r"n:(\d+)", rh)
                    node = m.group(1) if m else None
                if not node:
                    raise UnsupportedInput("Could not find a browse node (node=…) in the category URL")
                out.category_id = node
                out.keyword = (q.get("k") or [None])[0]
        elif data.input_type == InputType.KEYWORD:
            if not (data.keyword or "").strip():
                raise UnsupportedInput("Keyword is required")
            out.keyword = data.keyword.strip()[:200]  # type: ignore[union-attr]
        elif data.input_type == InputType.CATEGORY:
            if not (data.category or "").isdigit():
                raise UnsupportedInput("Amazon category must be a numeric browse node / classification ID")
            out.category_id = data.category
        elif data.input_type == InputType.LISTING_IDS:
            out.listing_ids = [i.upper() for i in data.listing_ids or [] if re.fullmatch(r"[A-Za-z0-9]{10}", i)]
            if not out.listing_ids:
                raise UnsupportedInput("Provide 10-character ASINs")
        out.source_limit_note = "Amazon Catalog search results are bounded by SP-API pagination limits."
        return out

    async def discover_products(self, data: ValidatedInput, cursor: str | None = None) -> DiscoveryPage:
        if data.product_id:
            return DiscoveryPage(external_ids=[data.product_id], total_estimate=1)
        if data.listing_ids:
            return DiscoveryPage(external_ids=data.listing_ids, total_estimate=len(data.listing_ids))
        _, marketplace_id, _ = REGIONS[data.marketplace_code]
        params: dict[str, Any] = {"marketplaceIds": marketplace_id, "includedData": "summaries", "pageSize": PAGE_SIZE}
        if data.keyword:
            params["keywords"] = data.keyword
        if data.category_id:
            params["classificationIds"] = data.category_id
        if cursor:
            params["pageToken"] = cursor
        payload = await self._get(data.marketplace_code, "/catalog/2022-04-01/items", params)
        token = (payload.get("pagination") or {}).get("nextToken")
        total = payload.get("numberOfResults")
        return DiscoveryPage(
            external_ids=[i["asin"] for i in payload.get("items") or []],
            next_cursor=token,
            total_estimate=total,
            source_limit_reached=bool(total and not token and total > PAGE_SIZE),
        )

    async def get_product_details(self, marketplace_code: str, external_product_id: str) -> NormalisedListing:
        _, marketplace_id, _ = REGIONS[marketplace_code]
        asin = external_product_id.upper()
        item = await self._get(
            marketplace_code,
            f"/catalog/2022-04-01/items/{quote(asin)}",
            {"marketplaceIds": marketplace_id, "includedData": CATALOG_INCLUDED},
        )
        offers: dict = {}
        try:
            offers = (
                await self._get(
                    marketplace_code,
                    f"/products/pricing/v0/items/{quote(asin)}/offers",
                    {"MarketplaceId": marketplace_id, "ItemCondition": "New"},
                )
            ).get("payload") or {}
        except ConnectorError:
            offers = {}  # pricing is optional; the listing is still recorded without a price
        return self._map(marketplace_code, asin, item, offers)

    def _map(self, marketplace_code: str, asin: str, item: dict, offers: dict) -> NormalisedListing:
        summary = (item.get("summaries") or [{}])[0]
        attributes = item.get("attributes") or {}
        identifiers = [i for group in item.get("identifiers") or [] for i in group.get("identifiers") or []]
        gtin = next((i["identifier"] for i in identifiers if i.get("identifierType") in ("EAN", "UPC", "GTIN")), None)
        ranks = (item.get("salesRanks") or [{}])[0]
        display_ranks = ranks.get("displayGroupRanks") or []
        class_ranks = ranks.get("classificationRanks") or []
        top_rank = (display_ranks or class_ranks or [{}])[0]
        images = [
            NormalisedImage(
                url=img["link"], image_type=img.get("variant"), width=img.get("width"), height=img.get("height")
            )
            for group in item.get("images") or []
            for img in group.get("images") or []
            if img.get("height", 0) >= 500 or img.get("variant") == "MAIN"
        ]
        children = [
            NormalisedVariation(external_variation_id=child, attributes={"relationship": "child"})
            for rel in item.get("relationships") or []
            for r in rel.get("relationships") or []
            for child in r.get("childAsins") or []
        ]
        parent = next(
            (
                p
                for rel in item.get("relationships") or []
                for r in rel.get("relationships") or []
                for p in r.get("parentAsins") or []
            ),
            None,
        )
        summary_offers = offers.get("Summary") or {}
        buy_box = (summary_offers.get("BuyBoxPrices") or [{}])[0]
        landed = buy_box.get("ListingPrice") or {}
        lowest = (summary_offers.get("LowestPrices") or [{}])[0].get("ListingPrice") or {}
        price_info = landed or lowest
        offer_count = sum(int(o.get("OfferCount", 0)) for o in summary_offers.get("NumberOfOffers") or [])
        metrics = [
            MetricValue(name="competitor_sales", measurement_type=MeasurementType.UNAVAILABLE, source="amazon:spapi")
        ]
        if top_rank.get("rank"):
            metrics.append(
                MetricValue(
                    name="sales_rank",
                    value=Decimal(top_rank["rank"]),
                    unit="rank",
                    measurement_type=MeasurementType.ACTUAL,
                    source="amazon:catalog",
                )
            )
        if summary_offers:
            metrics.append(
                MetricValue(
                    name="offer_count",
                    value=Decimal(offer_count),
                    unit="offers",
                    measurement_type=MeasurementType.ACTUAL,
                    source="amazon:pricing",
                )
            )
        host = "https://www.amazon.co.uk" if marketplace_code == "AMAZON_UK" else "https://www.amazon.com"
        return NormalisedListing(
            external_listing_id=asin,
            marketplace_code=marketplace_code,
            asin=asin,
            title=summary.get("itemName") or "(untitled)",
            product_url=f"{host}/dp/{asin}",
            parent_listing_id=parent,
            gtin=gtin,
            mpn=summary.get("partNumber") or summary.get("modelNumber"),
            description=_first_attr(attributes, "product_description"),
            brand=summary.get("brand") or summary.get("brandName"),
            category_path=(summary.get("browseClassification") or {}).get("displayName"),
            category_id=(summary.get("browseClassification") or {}).get("classificationId"),
            currency_code=price_info.get("CurrencyCode"),
            current_price=_dec(price_info.get("Amount")),
            stock_status=StockStatus.IN_STOCK if offer_count > 0 else StockStatus.UNKNOWN,
            stock_quantity_type=MeasurementType.UNAVAILABLE,
            sales_rank=top_rank.get("rank"),
            rank_category=top_rank.get("title"),
            attributes={"product_type": (item.get("productTypes") or [{}])[0].get("productType")},
            images=images,
            variations=children,
            metrics=metrics,
            endpoint="catalog/2022-04-01/items",
            source_reference=asin,
            retrieved_at=datetime.now(UTC),
        )

    # ---------------------------------------------------- own account
    def start_oauth(self, marketplace_code: str, state: str, redirect_uri: str) -> OAuthStart:
        s = get_settings()
        if not (self.is_configured() and s.amazon_spapi_app_id):
            raise NotConfigured(self.manifest.configuration_hint or "Amazon is not configured")
        query = urlencode({"application_id": s.amazon_spapi_app_id, "state": state, "redirect_uri": redirect_uri})
        return OAuthStart(
            authorization_url=f"{REGIONS[marketplace_code][2]}/apps/authorize/consent?{query}", state=state
        )

    async def complete_oauth(self, marketplace_code: str, params: dict[str, str], redirect_uri: str) -> OAuthResult:
        code = params.get("spapi_oauth_code")
        if not code:
            raise AuthenticationFailed("Amazon did not return an authorisation code")
        data = await self._lwa({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})
        seller = params.get("selling_partner_id")
        return OAuthResult(
            credentials=AccountCredentials(refresh_token=data["refresh_token"], external_account_id=seller),
            external_account_id=seller,
            granted_scopes=self.manifest.required_scopes,
        )

    async def list_orders(
        self, marketplace_code: str, credentials: AccountCredentials, updated_since: datetime, cursor: str | None
    ) -> OrderPage:
        token = await self._access_token(credentials.refresh_token or "")
        _, marketplace_id, _ = REGIONS[marketplace_code]
        params: dict[str, Any] = {"MarketplaceIds": marketplace_id, "MaxResultsPerPage": 100}
        if cursor:
            params["NextToken"] = cursor
        else:
            params["LastUpdatedAfter"] = updated_since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = (await self._get(marketplace_code, "/orders/v0/orders", params, token)).get("payload") or {}
        orders = []
        for o in payload.get("Orders") or []:
            items_payload = (
                await self._get(
                    marketplace_code, f"/orders/v0/orders/{quote(o['AmazonOrderId'])}/orderItems", {}, token
                )
            ).get("payload") or {}
            items = []
            for it in items_payload.get("OrderItems") or []:
                qty = int(it.get("QuantityOrdered") or 0)
                price = _dec((it.get("ItemPrice") or {}).get("Amount"))
                items.append(
                    OrderItemRecord(
                        external_order_item_id=it["OrderItemId"],
                        external_listing_id=it.get("ASIN"),
                        seller_sku=it.get("SellerSKU"),
                        quantity=qty,
                        unit_price=(price / qty) if price is not None and qty else price,
                        item_tax=_dec((it.get("ItemTax") or {}).get("Amount")),
                        currency_code=(it.get("ItemPrice") or {}).get("CurrencyCode"),
                    )
                )
            total = o.get("OrderTotal") or {}
            orders.append(
                OrderRecord(
                    external_order_id=o["AmazonOrderId"],
                    order_status=o.get("OrderStatus"),
                    order_currency=total.get("CurrencyCode"),
                    order_total=_dec(total.get("Amount")),
                    ordered_at=datetime.fromisoformat(o["PurchaseDate"].replace("Z", "+00:00"))
                    if o.get("PurchaseDate")
                    else None,
                    source_updated_at=datetime.fromisoformat(o["LastUpdateDate"].replace("Z", "+00:00"))
                    if o.get("LastUpdateDate")
                    else None,
                    items=items,
                )
            )
        return OrderPage(orders=orders, next_cursor=payload.get("NextToken"))

    async def aclose(self) -> None:
        await self.lwa.aclose()
        for c in self.clients.values():
            await c.aclose()
