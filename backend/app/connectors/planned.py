"""Connectors that are declared but require approved partner access before they can
run. They validate URLs (so users get precise feedback) and publish honest
capability manifests, but never return fabricated data."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.connectors.base import (
    CapabilityManifest,
    ConnectorInput,
    DiscoveryPage,
    InputType,
    MarketplaceConnector,
    MeasurementType,
    NormalisedListing,
    NotConfigured,
    RateLimitPolicy,
    ReleaseStatus,
    UnsupportedInput,
    ValidatedInput,
)
from app.core.ssrf import host_matches, normalise_url


class _PlannedConnector(MarketplaceConnector):
    product_patterns: tuple[re.Pattern[str], ...] = ()

    def is_configured(self) -> bool:
        return False

    async def validate_input(self, data: ConnectorInput) -> ValidatedInput:
        if data.marketplace_code not in self.manifest.marketplaces:
            raise UnsupportedInput(f"{data.marketplace_code} is not served by {self.manifest.display_name}")
        out = ValidatedInput(input_type=data.input_type, marketplace_code=data.marketplace_code, filters=data.filters)
        if data.url:
            url = normalise_url(data.url)
            if not host_matches(urlsplit(url).hostname or "", tuple(self.manifest.url_hosts)):
                raise UnsupportedInput(f"URL host must be one of: {', '.join(self.manifest.url_hosts)}")
            out.normalised_url = url
            for pattern in self.product_patterns:
                if m := pattern.search(url):
                    out.product_id = m.group(1)
        out.keyword = data.keyword
        raise NotConfigured(
            f"{self.manifest.display_name} requires an approved partner application. "
            f"{self.manifest.configuration_hint or ''}".strip()
        )

    async def discover_products(self, data: ValidatedInput, cursor: str | None = None) -> DiscoveryPage:
        raise NotConfigured(self.manifest.configuration_hint or "Connector not available")

    async def get_product_details(self, marketplace_code: str, external_product_id: str) -> NormalisedListing:
        raise NotConfigured(self.manifest.configuration_hint or "Connector not available")


class TikTokShopConnector(_PlannedConnector):
    product_patterns = (re.compile(r"/product/(\d{6,25})"), re.compile(r"/view/product/(\d{6,25})"))
    manifest = CapabilityManifest(
        key="tiktok",
        platform="tiktok",
        display_name="TikTok Shop (Partner API)",
        version="0.1.0",
        release_status=ReleaseStatus.PLANNED,
        marketplaces=["TIKTOK_US", "TIKTOK_UK"],
        url_hosts=["tiktok.com", "shop.tiktok.com"],
        supported_inputs=[InputType.PRODUCT_URL, InputType.KEYWORD],
        own_account_streams=["catalogue", "orders", "finance"],
        auth_type="TikTok Shop Partner Center app authorisation (HMAC-signed requests)",
        fields_available=["own-shop products, SKUs, inventory, orders and settlements (authorised shops)"],
        known_missing_fields=["competitor shop catalogues", "competitor sales"],
        metric_provenance={"competitor_sales": MeasurementType.UNAVAILABLE},
        pagination_style="page_token",
        rate_limit=RateLimitPolicy(requests_per_second=5, burst=5, max_concurrency=2),
        data_licence_note="Subject to TikTok Shop Partner terms; regional availability differs.",
        limitations=["Competitor research requires an approved affiliate/partner data programme."],
        configuration_hint="Register a TikTok Shop Partner app and add its app key/secret to the secrets manager.",
    )


class AliExpressConnector(_PlannedConnector):
    product_patterns = (re.compile(r"/item/(\d{6,25})\.html"),)
    manifest = CapabilityManifest(
        key="aliexpress",
        platform="aliexpress",
        display_name="AliExpress (Open Platform)",
        version="0.1.0",
        release_status=ReleaseStatus.PLANNED,
        marketplaces=["ALIEXPRESS_US", "ALIEXPRESS_UK"],
        url_hosts=["aliexpress.com", "aliexpress.us"],
        supported_inputs=[InputType.PRODUCT_URL, InputType.KEYWORD],
        auth_type="AliExpress Open Platform app key + signed requests",
        fields_available=["product details, SKUs, prices and shipping to destination country (approved apps)"],
        known_missing_fields=["sales rank", "exact competitor sales"],
        metric_provenance={"competitor_sales": MeasurementType.UNAVAILABLE},
        pagination_style="page_no/page_size",
        rate_limit=RateLimitPolicy(requests_per_second=2, burst=2, max_concurrency=2),
        data_licence_note="Subject to AliExpress Open Platform agreement.",
        limitations=["Destination country changes prices, shipping and availability."],
        configuration_hint="Apply for an AliExpress Open Platform (dropshipping/affiliate) app key.",
    )
