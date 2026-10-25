import json
from decimal import Decimal

import httpx
import pytest

from app.connectors.amazon import AmazonConnector
from app.connectors.base import (
    ConnectorInput,
    InputType,
    MeasurementType,
    NotConfigured,
    PermissionDenied,
    ProductUnavailable,
    RateLimited,
    RateLimitPolicy,
    StockStatus,
    TransientError,
    UnsupportedInput,
)
from app.connectors.demo import DemoConnector
from app.connectors.ebay import EbayConnector
from app.connectors.http import CircuitBreaker, ResilientClient, parse_retry_after
from app.connectors.planned import AliExpressConnector, TikTokShopConnector
from app.core.config import get_settings

FAST = RateLimitPolicy(requests_per_second=1000, burst=1000, max_concurrency=10)


async def no_sleep(_):
    return None


def client_for(handler, **kw):
    return ResilientClient("test", FAST, transport=httpx.MockTransport(handler), sleep=no_sleep, **kw)


async def test_retries_transient_errors_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    c = client_for(handler)
    assert await c.get_json("https://api.example.com/x") == {"ok": True}
    assert calls["n"] == 3


async def test_honours_retry_after_and_gives_up():
    slept = []

    async def record(s):
        slept.append(s)

    c = ResilientClient(
        "t",
        FAST,
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "7"})),
        sleep=record,
        max_retries=2,
    )
    with pytest.raises(RateLimited) as exc:
        await c.get_json("https://api.example.com/x")
    assert exc.value.retry_after == 7
    assert slept == [7, 7, 7]


@pytest.mark.parametrize("status,error", [(403, PermissionDenied), (404, ProductUnavailable)])
async def test_client_errors_not_retried(status, error):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status)

    with pytest.raises(error):
        await client_for(handler).get_json("https://api.example.com/x")
    assert calls["n"] == 1


def test_retry_after_http_date():
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("garbage") is None


def test_circuit_breaker_opens_and_half_opens():
    now = [0.0]
    b = CircuitBreaker(failure_threshold=2, reset_after=10, clock=lambda: now[0])
    b.record_failure()
    assert b.allow()
    b.record_failure()
    assert not b.allow()
    now[0] = 11
    assert b.allow()


async def test_circuit_open_raises_transient():
    c = client_for(lambda r: httpx.Response(500), max_retries=10)
    with pytest.raises(TransientError):
        await c.get_json("https://api.example.com/x")


# -------------------------------------------------------------------- demo
async def test_demo_connector_is_labelled_and_honest():
    d = DemoConnector()
    v = await d.validate_input(ConnectorInput(input_type=InputType.KEYWORD, marketplace_code="DEMO_UK", keyword="yoga"))
    page = await d.discover_products(v)
    listing = await d.get_product_details("DEMO_UK", page.external_ids[0])
    assert listing.title.startswith("[DEMO]")
    assert listing.currency_code == "GBP"
    by_name = {m.name: m for m in listing.metrics}
    assert by_name["competitor_sales"].measurement_type == MeasurementType.UNAVAILABLE
    assert by_name["competitor_sales"].value is None
    assert by_name["estimated_monthly_units"].methodology


async def test_demo_rejects_foreign_hosts():
    with pytest.raises(UnsupportedInput):
        await DemoConnector().validate_input(
            ConnectorInput(
                input_type=InputType.SHOP_URL, marketplace_code="DEMO_US", url="https://evil.example.com/shop/abc"
            )
        )


def test_estimated_metric_requires_methodology():
    from app.connectors.base import MetricValue

    with pytest.raises(ValueError):
        MetricValue(name="x", value=Decimal(1), measurement_type=MeasurementType.ESTIMATED, source="s")


# -------------------------------------------------------------------- eBay
@pytest.fixture
def ebay_configured(monkeypatch):
    from pydantic import SecretStr

    s = get_settings()
    monkeypatch.setattr(s, "ebay_client_id", "cid")
    monkeypatch.setattr(s, "ebay_client_secret", SecretStr("csecret"))
    return s


@pytest.mark.parametrize(
    "url,field,expected",
    [
        ("https://www.ebay.co.uk/itm/Some-Title/123456789012?hash=x", "product_id", "123456789012"),
        ("https://www.ebay.com/itm/987654321098", "product_id", "987654321098"),
        ("https://www.ebay.co.uk/b/Yoga-Mats/158932/bn_1234", "category_id", "158932"),
    ],
)
async def test_ebay_url_parsing(url, field, expected):
    input_type = InputType.PRODUCT_URL if field == "product_id" else InputType.CATEGORY_URL
    v = await EbayConnector().validate_input(ConnectorInput(input_type=input_type, marketplace_code="EBAY_UK", url=url))
    assert getattr(v, field) == expected


async def test_ebay_shop_needs_keyword_or_category():
    c = EbayConnector()
    with pytest.raises(UnsupportedInput):
        await c.validate_input(
            ConnectorInput(
                input_type=InputType.SHOP_URL, marketplace_code="EBAY_US", url="https://www.ebay.com/str/greatshop"
            )
        )
    v = await c.validate_input(
        ConnectorInput(
            input_type=InputType.SHOP_URL,
            marketplace_code="EBAY_US",
            url="https://www.ebay.com/str/greatshop",
            keyword="lamp",
        )
    )
    assert v.seller_id == "greatshop" and v.keyword == "lamp"


async def test_ebay_discovery_and_mapping(ebay_configured):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path == "/identity/v1/oauth2/token":
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "apptoken", "expires_in": 7200})
        assert request.headers["Authorization"] == "Bearer apptoken"
        assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_GB"
        if request.url.path.endswith("/item_summary/search"):
            assert request.url.params["filter"] == "sellers:{greatshop}"
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "itemSummaries": [
                        {"itemId": "v1|111111111111|0", "legacyItemId": "111111111111"},
                        {"itemId": "v1|222222222222|0", "legacyItemId": "222222222222"},
                    ],
                },
            )
        if request.url.path.endswith("/get_item_by_legacy_id"):
            return httpx.Response(
                200,
                json={
                    "itemId": "v1|111111111111|0",
                    "title": "Cork Yoga Block",
                    "price": {"value": "12.99", "currency": "GBP"},
                    "itemWebUrl": "https://www.ebay.co.uk/itm/111111111111",
                    "brand": "Zen",
                    "gtin": "5012345678900",
                    "categoryPath": "Sporting Goods|Yoga",
                    "image": {"imageUrl": "https://i.ebayimg.com/1.jpg"},
                    "estimatedAvailabilities": [
                        {
                            "estimatedAvailabilityStatus": "LIMITED_STOCK",
                            "estimatedAvailableQuantity": 3,
                            "estimatedSoldQuantity": 41,
                        }
                    ],
                    "seller": {"username": "greatshop"},
                    "condition": "New",
                },
            )
        return httpx.Response(404)

    c = EbayConnector(transport=httpx.MockTransport(handler))
    v = await c.validate_input(
        ConnectorInput(
            input_type=InputType.SHOP_URL,
            marketplace_code="EBAY_UK",
            url="https://www.ebay.co.uk/usr/greatshop",
            keyword="yoga",
        )
    )
    page = await c.discover_products(v)
    assert page.external_ids == ["111111111111", "222222222222"] and page.next_cursor is None
    listing = await c.get_product_details("EBAY_UK", "111111111111")
    assert listing.current_price == Decimal("12.99") and listing.currency_code == "GBP"
    assert listing.stock_status == StockStatus.LOW_STOCK
    assert listing.stock_quantity == 3 and listing.stock_quantity_type == MeasurementType.ESTIMATED
    assert listing.category_path == "Sporting Goods > Yoga"
    sold = next(m for m in listing.metrics if m.name == "estimated_sold_quantity")
    assert sold.measurement_type == MeasurementType.ESTIMATED and sold.value == 41
    # token fetched once and reused
    assert sum(1 for r in seen if r.url.path == "/identity/v1/oauth2/token") == 1


async def test_ebay_not_configured():
    with pytest.raises(NotConfigured):
        await EbayConnector().get_product_details("EBAY_US", "123456789012")


# ------------------------------------------------------------------ Amazon
async def test_amazon_rejects_competitor_shop_urls():
    with pytest.raises(UnsupportedInput, match="storefront"):
        await AmazonConnector().validate_input(
            ConnectorInput(
                input_type=InputType.SHOP_URL, marketplace_code="AMAZON_UK", url="https://www.amazon.co.uk/s?me=A123"
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.amazon.co.uk/Some-Product/dp/B08N5WRWNW/ref=sr_1_1",
        "https://www.amazon.co.uk/gp/product/B08N5WRWNW",
        "amazon.co.uk/dp/B08N5WRWNW?th=1",
    ],
)
async def test_amazon_asin_parsing(url):
    v = await AmazonConnector().validate_input(
        ConnectorInput(input_type=InputType.PRODUCT_URL, marketplace_code="AMAZON_UK", url=url)
    )
    assert v.product_id == "B08N5WRWNW"


async def test_amazon_mapping(monkeypatch):
    from pydantic import SecretStr

    s = get_settings()
    monkeypatch.setattr(s, "amazon_lwa_client_id", "id")
    monkeypatch.setattr(s, "amazon_lwa_client_secret", SecretStr("sec"))
    monkeypatch.setattr(s, "amazon_research_refresh_token", SecretStr("rt"))

    def handler(request: httpx.Request):
        if request.url.host == "api.amazon.com":
            body = dict(x.split("=") for x in request.content.decode().split("&"))
            assert body["grant_type"] == "refresh_token"
            return httpx.Response(200, json={"access_token": "atk", "expires_in": 3600})
        assert request.url.host == "sellingpartnerapi-eu.amazon.com"
        assert request.headers["x-amz-access-token"] == "atk"
        if "/offers" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "Summary": {
                            "BuyBoxPrices": [{"ListingPrice": {"Amount": 19.99, "CurrencyCode": "GBP"}}],
                            "NumberOfOffers": [{"OfferCount": 3}, {"OfferCount": 1}],
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "asin": "B08N5WRWNW",
                "summaries": [
                    {
                        "itemName": "Yoga Mat 6mm",
                        "brand": "Zen",
                        "browseClassification": {"displayName": "Yoga Mats", "classificationId": "123"},
                    }
                ],
                "identifiers": [{"identifiers": [{"identifierType": "EAN", "identifier": "5012345678900"}]}],
                "salesRanks": [{"displayGroupRanks": [{"title": "Sports", "rank": 1520}]}],
                "images": [
                    {"images": [{"link": "https://m.media-amazon.com/1.jpg", "variant": "MAIN", "height": 1000}]}
                ],
            },
        )

    c = AmazonConnector(transport=httpx.MockTransport(handler))
    listing = await c.get_product_details("AMAZON_UK", "B08N5WRWNW")
    assert listing.title == "Yoga Mat 6mm" and listing.gtin == "5012345678900"
    assert listing.current_price == Decimal("19.99") and listing.sales_rank == 1520
    assert listing.product_url == "https://www.amazon.co.uk/dp/B08N5WRWNW"
    metrics = {m.name: m for m in listing.metrics}
    assert metrics["offer_count"].value == 4
    assert metrics["sales_rank"].measurement_type == MeasurementType.ACTUAL
    assert metrics["competitor_sales"].measurement_type == MeasurementType.UNAVAILABLE


async def test_planned_connectors_never_return_data():
    for connector, code, url in [
        (TikTokShopConnector(), "TIKTOK_UK", "https://shop.tiktok.com/view/product/1729384756"),
        (AliExpressConnector(), "ALIEXPRESS_US", "https://www.aliexpress.com/item/1005001234567.html"),
    ]:
        assert not connector.is_configured()
        with pytest.raises(NotConfigured):
            await connector.validate_input(
                ConnectorInput(input_type=InputType.PRODUCT_URL, marketplace_code=code, url=url)
            )
    json.dumps(TikTokShopConnector().describe().model_dump(mode="json"))
