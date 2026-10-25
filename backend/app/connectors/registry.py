"""Connector registry: maps platforms to connector instances."""

from __future__ import annotations

from collections.abc import Callable

from app.connectors.amazon import AmazonConnector
from app.connectors.base import CapabilityManifest, MarketplaceConnector, NotConfigured
from app.connectors.demo import DemoConnector
from app.connectors.ebay import EbayConnector
from app.connectors.planned import AliExpressConnector, TikTokShopConnector

_FACTORIES: dict[str, Callable[[], MarketplaceConnector]] = {
    "demo": DemoConnector,
    "ebay": EbayConnector,
    "amazon": AmazonConnector,
    "tiktok": TikTokShopConnector,
    "aliexpress": AliExpressConnector,
}
_overrides: dict[str, MarketplaceConnector] = {}


def platforms() -> list[str]:
    return list(_FACTORIES)


def get_connector(platform: str) -> MarketplaceConnector:
    """Return a fresh connector (each holds its own HTTP client / rate limiter)."""
    if platform in _overrides:
        return _overrides[platform]
    factory = _FACTORIES.get(platform)
    if factory is None:
        raise NotConfigured(f"No connector registered for platform {platform!r}")
    return factory()


def manifests() -> list[CapabilityManifest]:
    return [_FACTORIES[p]().describe() if p not in _overrides else _overrides[p].describe() for p in _FACTORIES]


def override_connector(platform: str, connector: MarketplaceConnector | None) -> None:
    """Testing hook: substitute a connector instance for a platform."""
    if connector is None:
        _overrides.pop(platform, None)
    else:
        _overrides[platform] = connector
