"""Demo connector: deterministic, clearly-labelled synthetic data for development,
demos and automated tests. Every title is prefixed with ``[DEMO]`` and every metric
declares ``synthetic`` methodology so it can never be mistaken for live data."""

from __future__ import annotations

import hashlib
import random
import re
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import parse_qs, urlsplit

from app.connectors.base import (
    AccountCredentials,
    CapabilityManifest,
    ConnectorInput,
    DiscoveryPage,
    InputType,
    MarketplaceConnector,
    MeasurementType,
    MetricValue,
    NormalisedImage,
    NormalisedListing,
    NormalisedVariation,
    OAuthResult,
    OAuthStart,
    OrderItemRecord,
    OrderPage,
    OrderRecord,
    ProductUnavailable,
    RateLimitPolicy,
    ReleaseStatus,
    SourceType,
    StockStatus,
    TransientError,
    UnsupportedInput,
    ValidatedInput,
)
from app.core.config import get_settings
from app.core.ssrf import host_matches, normalise_url
from app.services.identifiers import make_gtin13

DEMO_HOST = "demo-marketplace.example"
CURRENCY = {"DEMO_US": "USD", "DEMO_UK": "GBP"}
FX = {"DEMO_US": Decimal("1.00"), "DEMO_UK": Decimal("0.79")}

BRANDS = ["Northwind", "Contoso", "Fabrikam", "Tailspin", "Litware", "Adatum", "Proseware", "Wingtip"]
CATEGORIES = {
    "kitchen": ["Kitchen", "Cookware"],
    "fitness": ["Sports", "Fitness"],
    "electronics": ["Electronics", "Accessories"],
    "home": ["Home", "Storage"],
    "pets": ["Pet Supplies", "Dogs"],
}
NOUNS = {
    "kitchen": ["Non-Stick Frying Pan", "Chef Knife", "Silicone Spatula Set", "Cast Iron Skillet", "Salad Spinner"],
    "fitness": ["Resistance Bands", "Yoga Mat", "Adjustable Dumbbell", "Skipping Rope", "Foam Roller"],
    "electronics": ["USB-C Charger", "Wireless Earbuds", "Phone Stand", "HDMI Cable", "Power Bank"],
    "home": ["Storage Basket", "Drawer Organiser", "Vacuum Storage Bags", "Shoe Rack", "Laundry Hamper"],
    "pets": ["Dog Lead", "Chew Toy", "Pet Bed", "Grooming Brush", "Travel Water Bottle"],
}
PAGE_SIZE = 20
CATALOGUE_SIZE = 5000  # synthetic universe; discovery is bounded like a real source
MAX_RESULTS = 400


def _rng(*parts: object) -> random.Random:
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def _money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _category_for(seed: int) -> str:
    return list(CATEGORIES)[seed % len(CATEGORIES)]


class DemoConnector(MarketplaceConnector):
    manifest = CapabilityManifest(
        key="demo",
        platform="demo",
        display_name="Demo Marketplace (synthetic data)",
        version="1.0.0",
        release_status=ReleaseStatus.SANDBOX,
        marketplaces=["DEMO_US", "DEMO_UK"],
        url_hosts=[DEMO_HOST],
        supported_inputs=[
            InputType.SHOP_URL,
            InputType.CATEGORY_URL,
            InputType.PRODUCT_URL,
            InputType.KEYWORD,
            InputType.CATEGORY,
            InputType.LISTING_IDS,
        ],
        own_account_streams=["orders", "catalogue"],
        auth_type="none (simulated OAuth for account connections)",
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
            "variations",
            "stock_status",
            "rating",
            "review_count",
            "sales_rank",
            "seller",
        ],
        known_missing_fields=["actual competitor sales"],
        metric_provenance={
            "sales_rank": MeasurementType.ACTUAL,
            "estimated_monthly_units": MeasurementType.ESTIMATED,
            "competitor_sales": MeasurementType.UNAVAILABLE,
        },
        pagination_style="cursor (page number)",
        max_results_per_query=MAX_RESULTS,
        rate_limit=RateLimitPolicy(requests_per_second=50, burst=50, max_concurrency=8),
        data_licence_note="Synthetic data generated locally. Not real marketplace data.",
        limitations=[
            f"Discovery returns at most {MAX_RESULTS} products per query (simulated source limit).",
            "All values are synthetic and prefixed [DEMO].",
        ],
        sandbox_supported=True,
    )

    def __init__(self, failure_ids: set[str] | None = None):
        # IDs that fail transiently on their first attempt (exercises retry logic in tests).
        self.failure_ids = failure_ids or set()
        self._failed_once: set[str] = set()

    def is_configured(self) -> bool:
        return get_settings().enable_demo_connector

    # ------------------------------------------------------------ input
    async def validate_input(self, data: ConnectorInput) -> ValidatedInput:
        if data.marketplace_code not in self.manifest.marketplaces:
            raise UnsupportedInput(f"Marketplace {data.marketplace_code} is not served by the demo connector")
        result = ValidatedInput(
            input_type=data.input_type, marketplace_code=data.marketplace_code, filters=data.filters
        )
        if data.input_type in (InputType.SHOP_URL, InputType.CATEGORY_URL, InputType.PRODUCT_URL):
            if not data.url:
                raise UnsupportedInput("A URL is required for this input type")
            url = normalise_url(data.url)
            parts = urlsplit(url)
            if not host_matches(parts.hostname or "", (DEMO_HOST,)):
                raise UnsupportedInput(f"URL host must be {DEMO_HOST} for the demo marketplace")
            result.normalised_url = url
            path = parts.path.rstrip("/")
            if data.input_type == InputType.SHOP_URL:
                m = re.fullmatch(r"/shop/([A-Za-z0-9_-]{2,64})", path)
                if not m:
                    raise UnsupportedInput("Shop URLs look like https://demo-marketplace.example/shop/<seller>")
                result.seller_id = m.group(1)
            elif data.input_type == InputType.CATEGORY_URL:
                m = re.fullmatch(r"/c/([a-z]+)", path)
                if not m or m.group(1) not in CATEGORIES:
                    raise UnsupportedInput(f"Category URLs look like /c/<one of {', '.join(CATEGORIES)}>")
                result.category_id = m.group(1)
            else:
                m = re.fullmatch(r"/p/(DEMO-\d{1,6})", path)
                if not m:
                    raise UnsupportedInput("Product URLs look like https://demo-marketplace.example/p/DEMO-123")
                result.product_id = m.group(1)
            q = parse_qs(parts.query).get("q")
            if q:
                result.keyword = q[0][:200]
        elif data.input_type == InputType.KEYWORD:
            if not data.keyword or not data.keyword.strip():
                raise UnsupportedInput("Keyword is required")
            result.keyword = data.keyword.strip()[:200]
        elif data.input_type == InputType.CATEGORY:
            if (data.category or "") not in CATEGORIES:
                raise UnsupportedInput(f"Category must be one of: {', '.join(CATEGORIES)}")
            result.category_id = data.category
        elif data.input_type == InputType.LISTING_IDS:
            if not data.listing_ids:
                raise UnsupportedInput("At least one listing ID is required")
            result.listing_ids = [i.strip() for i in data.listing_ids if i.strip()][:1000]
        result.source_limit_note = f"The demo source returns at most {MAX_RESULTS} results per query."
        return result

    # -------------------------------------------------------- discovery
    def _universe(self, data: ValidatedInput) -> list[int]:
        if data.product_id:
            return [int(data.product_id.split("-")[1])]
        if data.listing_ids:
            return [int(i.split("-")[1]) for i in data.listing_ids if re.fullmatch(r"DEMO-\d{1,6}", i)]
        rng = _rng("universe", data.keyword, data.seller_id, data.category_id)
        size = rng.randint(60, MAX_RESULTS + 200)
        ids = rng.sample(range(1, CATALOGUE_SIZE), size)
        if data.category_id:
            ids = [i for i in range(1, CATALOGUE_SIZE) if _category_for(i) == data.category_id]
            ids = _rng("cat", data.category_id).sample(ids, min(len(ids), MAX_RESULTS + 50))
        if data.keyword:
            words = data.keyword.lower().split()
            matching = [i for i in range(1, CATALOGUE_SIZE) if any(w in self._title(i).lower() for w in words)]
            if matching:
                ids = matching
        return ids

    async def discover_products(self, data: ValidatedInput, cursor: str | None = None) -> DiscoveryPage:
        universe = self._universe(data)
        limited = len(universe) > MAX_RESULTS
        universe = universe[:MAX_RESULTS]
        page = int(cursor or 0)
        chunk = universe[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        has_more = (page + 1) * PAGE_SIZE < len(universe)
        return DiscoveryPage(
            external_ids=[f"DEMO-{i}" for i in chunk],
            next_cursor=str(page + 1) if has_more else None,
            total_estimate=len(universe),
            source_limit_reached=limited and not has_more,
            note=f"Source limit of {MAX_RESULTS} results reached" if limited and not has_more else None,
        )

    # ---------------------------------------------------------- details
    def _title(self, seed: int) -> str:
        rng = _rng("product", seed)
        cat = _category_for(seed)
        suffix = rng.choice(["Pro", "Lite", "Max", "Plus", ""])
        return f"[DEMO] {rng.choice(BRANDS)} {rng.choice(NOUNS[cat])} {suffix}".strip()

    async def get_product_details(self, marketplace_code: str, external_product_id: str) -> NormalisedListing:
        m = re.fullmatch(r"DEMO-(\d{1,6})", external_product_id)
        if not m:
            raise ProductUnavailable("Unknown demo product id")
        seed = int(m.group(1))
        if seed % 97 == 0:
            raise ProductUnavailable("Listing removed by seller (simulated)")
        if external_product_id in self.failure_ids and external_product_id not in self._failed_once:
            self._failed_once.add(external_product_id)
            raise TransientError("Simulated transient source failure")

        rng = _rng("product", seed)
        cat = _category_for(seed)
        brand = rng.choice(BRANDS)
        title = self._title(seed)
        base_usd = rng.uniform(6, 140)
        # Region-specific price variance so cross-marketplace comparison is meaningful.
        regional = _rng("price", seed, marketplace_code).uniform(0.9, 1.15)
        price = _money(base_usd * float(FX[marketplace_code]) * regional)
        currency = CURRENCY[marketplace_code]
        stock_roll = _rng("stock", seed, marketplace_code, datetime.now(UTC).date()).random()
        stock = (
            StockStatus.OUT_OF_STOCK
            if stock_roll < 0.08
            else StockStatus.LOW_STOCK
            if stock_roll < 0.2
            else StockStatus.IN_STOCK
        )
        rank = rng.randint(50, 250_000)
        seller = f"seller{seed % 37}"
        gtin = make_gtin13(f"50{seed:010d}")  # identical across DEMO_US/DEMO_UK -> exact matches
        colours = rng.sample(["Black", "White", "Grey", "Blue", "Red", "Green"], k=rng.randint(0, 3))
        sizes = rng.sample(["S", "M", "L", "XL"], k=rng.randint(0, 2)) if cat == "fitness" else []
        variations = [
            NormalisedVariation(
                external_variation_id=f"{external_product_id}-{c[:2].upper()}{('-' + s) if s else ''}",
                variation_sku=f"SKU-{seed}-{c[:2].upper()}{s}",
                colour=c,
                size=s or None,
                price=_money(float(price) * _rng("var", seed, c, s).uniform(0.95, 1.1)),
                currency_code=currency,
                stock_status=StockStatus.IN_STOCK,
            )
            for c in colours
            for s in (sizes or [""])
        ]
        estimated_units = int(max(1, 30000 / (rank**0.55)))
        images = [
            NormalisedImage(url=f"https://{DEMO_HOST}/img/{seed}/{i}.jpg", image_type="main" if i == 0 else "gallery")
            for i in range(rng.randint(1, 5))
        ]
        now = datetime.now(UTC)
        return NormalisedListing(
            external_listing_id=external_product_id,
            marketplace_code=marketplace_code,
            title=title,
            product_url=f"https://{DEMO_HOST}/p/{external_product_id}",
            description=f"Synthetic demo description for {title}. Not a real product.",
            brand=brand,
            gtin=gtin,
            mpn=f"{brand[:3].upper()}-{seed:05d}",
            category_path=" > ".join(CATEGORIES[cat]),
            category_id=cat,
            currency_code=currency,
            current_price=price,
            original_price=_money(float(price) * 1.2) if rng.random() < 0.3 else None,
            shipping_price=Decimal("0.00") if rng.random() < 0.6 else _money(rng.uniform(1.5, 6)),
            stock_status=stock,
            stock_quantity=None,
            stock_quantity_type=MeasurementType.UNAVAILABLE,
            condition="new",
            rating=Decimal(str(round(rng.uniform(3.2, 4.9), 1))),
            review_count=rng.randint(0, 8000),
            sales_rank=rank,
            rank_category=CATEGORIES[cat][0],
            seller_external_id=seller,
            seller_name=f"Demo Seller {seed % 37}",
            fulfilment_type=rng.choice(["merchant", "marketplace"]),
            attributes={"material": rng.choice(["steel", "plastic", "cotton", "silicone"]), "synthetic": True},
            images=images,
            variations=variations,
            metrics=[
                MetricValue(
                    name="sales_rank",
                    value=Decimal(rank),
                    unit="rank",
                    measurement_type=MeasurementType.ACTUAL,
                    source="demo:catalogue",
                ),
                MetricValue(
                    name="estimated_monthly_units",
                    value=Decimal(estimated_units),
                    unit="units/month",
                    measurement_type=MeasurementType.ESTIMATED,
                    methodology="synthetic power-law rank model (demo only)",
                    confidence=Decimal("0.300"),
                    period_start=now - timedelta(days=30),
                    period_end=now,
                    source="demo:estimator",
                ),
                MetricValue(
                    name="competitor_sales", measurement_type=MeasurementType.UNAVAILABLE, source="demo:catalogue"
                ),
            ],
            endpoint="demo:get_product_details",
            source_reference=f"demo://{marketplace_code}/{external_product_id}",
            retrieved_at=now,
        )

    # ---------------------------------------------------- own account
    def start_oauth(self, marketplace_code: str, state: str, redirect_uri: str) -> OAuthStart:
        return OAuthStart(authorization_url=f"{redirect_uri}?state={state}&code=demo-code", state=state)

    async def complete_oauth(self, marketplace_code: str, params: dict[str, str], redirect_uri: str) -> OAuthResult:
        if params.get("code") != "demo-code":
            raise UnsupportedInput("Invalid demo authorisation code")
        return OAuthResult(
            credentials=AccountCredentials(
                refresh_token="demo-refresh-token",  # noqa: S106 - synthetic demo credential
                external_account_id="DEMO-SELLER-1",
            ),
            external_account_id="DEMO-SELLER-1",
            granted_scopes=["catalogue:read", "orders:read"],
        )

    async def list_account_listings(
        self, marketplace_code: str, credentials: AccountCredentials, cursor: str | None
    ) -> tuple[list[NormalisedListing], str | None]:
        page = int(cursor or 0)
        ids = list(range(3000 + page * 10, 3000 + page * 10 + 10))
        listings = []
        for i in ids:
            listing = await self.get_product_details(marketplace_code, f"DEMO-{i}") if i % 97 else None
            if listing:
                listing.source_type = SourceType.OWN_ACCOUNT
                listing.seller_sku = f"OWN-{i}"
                listing.stock_quantity = _rng("qty", i).randint(0, 300)
                listing.stock_quantity_type = MeasurementType.ACTUAL
                listings.append(listing)
        return listings, (str(page + 1) if page < 2 else None)

    async def list_orders(
        self, marketplace_code: str, credentials: AccountCredentials, updated_since: datetime, cursor: str | None
    ) -> OrderPage:
        page = int(cursor or 0)
        currency = CURRENCY[marketplace_code]
        orders = []
        for n in range(page * 25, page * 25 + 25):
            rng = _rng("order", marketplace_code, n)
            items = []
            for k in range(rng.randint(1, 3)):
                listing_seed = 3000 + rng.randint(0, 29)
                if listing_seed % 97 == 0:
                    listing_seed += 1
                unit = _money(rng.uniform(8, 90))
                qty = rng.randint(1, 3)
                items.append(
                    OrderItemRecord(
                        external_order_item_id=f"OI-{n}-{k}",
                        external_listing_id=f"DEMO-{listing_seed}",
                        seller_sku=f"OWN-{listing_seed}",
                        quantity=qty,
                        unit_price=unit,
                        item_tax=_money(float(unit) * qty * 0.2) if currency == "GBP" else Decimal("0"),
                        marketplace_fees=_money(float(unit) * qty * 0.15),
                        refunded_amount=unit if rng.random() < 0.05 else Decimal("0"),
                        currency_code=currency,
                    )
                )
            ordered = datetime.now(UTC) - timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 23))
            orders.append(
                OrderRecord(
                    external_order_id=f"DEMO-ORD-{n:06d}",
                    order_status=rng.choice(["shipped", "shipped", "shipped", "delivered", "cancelled"]),
                    order_currency=currency,
                    order_total=sum((i.unit_price or Decimal(0)) * i.quantity for i in items),
                    ordered_at=ordered,
                    source_updated_at=ordered + timedelta(hours=2),
                    items=items,
                )
            )
        return OrderPage(orders=orders, next_cursor=str(page + 1) if page < 3 else None)
