# Connectors

Each marketplace has one connector that implements `MarketplaceConnector`
(`backend/app/connectors/base.py`):

```python
class MarketplaceConnector(abc.ABC):
    manifest: CapabilityManifest
    def is_configured(self) -> bool
    async def validate_input(self, data: ConnectorInput) -> ValidatedInput
    async def discover_products(self, data: ValidatedInput, cursor: str | None = None) -> DiscoveryPage
    async def get_product_details(self, marketplace_code: str, external_product_id: str) -> NormalisedListing
    async def get_variations(...) -> list[NormalisedVariation]
    async def get_availability(...) -> Availability
    # own account (optional)
    def start_oauth(...) / async def complete_oauth(...)
    async def list_orders(...) -> OrderPage
    async def list_account_listings(...)
```

Results are typed Pydantic models, not loose dicts. A `MetricValue` with
`measurement_type="estimated"` is rejected unless it declares a methodology, and an
`unavailable` metric always has a `None` value.

## Capability matrix

The live matrix is served at `GET /api/v1/connectors` and shown in the app under
*Marketplace Connections*.

| | Demo | eBay | Amazon | TikTok Shop | AliExpress |
|---|---|---|---|---|---|
| Status | sandbox | beta | beta | planned | planned |
| Marketplaces | DEMO_US, DEMO_UK | EBAY_US, EBAY_UK | AMAZON_US, AMAZON_UK | TIKTOK_US/UK | ALIEXPRESS_US/UK |
| API | synthetic | Buy Browse, Sell Fulfillment | SP-API Catalog 2022-04-01, Pricing v0, Orders v0 | Partner API | Open Platform |
| Shop URL | yes | yes (needs a keyword or category) | **no**: no storefront API | – | – |
| Category / keyword / product URL | yes | yes | yes (browse node / keywords / ASIN) | – | – |
| Max results per query | 400 (simulated) | 10,000 | bounded by pagination | – | – |
| Sales rank | actual | not provided | actual | – | – |
| Sales figures | estimated (synthetic model) | `estimatedSoldQuantity` (estimated) | not provided | – | – |
| Competitor sales | **unavailable** | **unavailable** | **unavailable** | **unavailable** | **unavailable** |
| Stock quantity | unavailable | estimated (`estimatedAvailableQuantity`) | unavailable | – | – |
| Own-account streams | catalogue, orders | orders | orders | catalogue, orders, finance (planned) | – |
| Default rate limit | 50 req/s | 4 req/s, 4 concurrent | 1.5 req/s, 2 concurrent | – | – |

*Planned* connectors validate URLs and publish manifests, but they raise
`NotConfigured` and never return data until a partner application is approved and
implemented.

## Adding a connector

1. Subclass `MarketplaceConnector` and write an honest `CapabilityManifest`: list
   `known_missing_fields` and classify every metric.
2. Use `ResilientClient` for HTTP (rate limit, retries, `Retry-After`, circuit breaker).
3. Validate URLs against the manifest's `url_hosts`. Never fetch a user-supplied URL directly.
4. Register the connector in `connectors/registry.py` and add the marketplace rows in a migration.
5. Add tests with `httpx.MockTransport` for URL parsing, field mapping, pagination,
   permission errors and rate limits. Contract tests in `tests/test_connectors.py` show the pattern.

## Configuration

| Variable | Purpose |
|---|---|
| `MPI_EBAY_CLIENT_ID`, `MPI_EBAY_CLIENT_SECRET` | eBay application keys (Browse API uses client credentials) |
| `MPI_EBAY_RU_NAME` | eBay redirect name for seller authorisation |
| `MPI_EBAY_ENVIRONMENT` | `production` or `sandbox` |
| `MPI_AMAZON_LWA_CLIENT_ID`, `MPI_AMAZON_LWA_CLIENT_SECRET` | Login with Amazon credentials |
| `MPI_AMAZON_SPAPI_APP_ID` | SP-API application ID (seller consent flow) |
| `MPI_AMAZON_RESEARCH_REFRESH_TOKEN` | Refresh token of an authorised seller, used for catalogue research |

Seller-account OAuth redirects to `{MPI_PUBLIC_API_URL}/api/v1/connections/callback/{platform}`.
The resulting tokens are stored in the secrets store and are never sent to clients.
