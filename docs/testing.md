# Testing, benchmarks and acceptance

## Automated tests

`backend/tests` has 77 tests that run against a real PostgreSQL database (the schema is
recreated per session from the migrations).

| Spec area (13.1) | Tests |
|---|---|
| URL validation, connector permissions, pagination, field mapping | `test_connectors.py` (eBay and Amazon mapping via mocked transports, URL parsing, planned connectors), `test_units.py` (SSRF) |
| Duplicate detection, canonical matching, variations | `test_jobs.py::test_rerun_is_idempotent…`, `test_products_and_analytics.py::test_gtin_matching_and_review` |
| SQL constraints, migrations, transactions, upsert idempotency | session fixture runs migrations; idempotency and crash-recovery tests |
| Retry and backoff, rate limiting, cancellation and resumption, partial success | `test_connectors.py` (retry, `Retry-After`, circuit breaker), `test_jobs.py` (transient retry, partial and resume, crash recovery, cancel) |
| Currency conversion, profit formulas, zero denominators, refunds | `test_units.py` (VAT, break-even and target-margin consistency, guards), `test_products_and_analytics.py` (FX-converted costs), `test_account_sync.py` (refund-aware own-account summary) |
| Org isolation, roles, credential protection, SSRF | `test_api_auth.py`, `test_account_sync.py` (secret reference, deletion), `test_units.py` |
| Export correctness, memory behaviour, formula safety | `test_products_and_analytics.py::test_exports_xlsx_and_csv` (sheets, injection, tampered links); exports stream in keyset chunks |

The web app is type-checked (`tsc --noEmit`) and built in CI. The desktop crate has unit
tests (`cargo test`). End-to-end checks were done with Playwright against a full local
stack (API + Celery worker + static web build): sign-in, research job through the worker,
product detail tabs, Excel download, demo seller OAuth and order sync.

## Million-record benchmark

`python -m scripts.benchmark_scale --listings 1000000 --observations-per-listing 3`

Measured on the development container (single PostgreSQL 16 instance, default settings, shared CPU):

| Step | Result |
|---|---|
| Bulk load 1,000,000 listings (server-side, all indexes live) | ~90–120 s |
| Bulk load 3,000,000 observations | ~16 s |
| Refresh dashboard materialised view | 1.7 s |
| Application ingest path (batched upserts + provenance + history) | ~850 listings/s on one worker |
| First page, newest updated | 3.7 ms |
| Full-text search "yoga mat" | 13 ms |
| Price range + currency + stock filter, price sort | 4 ms |
| Deep keyset page (page 21) | 3.6 ms |
| Category contains + brand | 4.9 ms |
| Capped count (≤ 10,000) | 78 ms |
| Dashboard overview (materialised view) | 170 ms |

Do not promise extraction speed from these figures. Real throughput is bounded by each
marketplace's quotas and response sizes (for example around 1–4 requests per second), not by
the database.

## Acceptance criteria (spec 14.1)

| Criterion | Status |
|---|---|
| Submit a shop URL, category, keyword or product URL and start a supported job | ✅ API, UI and tests |
| Jobs show accurate progress, per-item outcomes, retry state and resumable checkpoints | ✅ |
| Records stored with source, timestamp, identifiers and duplicate protection | ✅ provenance table, unique keys, idempotent upserts |
| Same account and project data from Windows and cloud dashboard | ✅ one API and one UI build; desktop compiled here on Linux, Windows installer built by CI |
| Competitor and own-account records are distinguishable and permission-protected | ✅ |
| Imports and queries ≥ 1M synthetic listings | ✅ benchmark above |
| A smaller live dataset processed through an authorised connector | ⏳ needs your eBay or Amazon credentials. Connector mapping is verified against documented API payloads in tests |
| Historical prices and metrics retained and queried correctly | ✅ |
| Exports: selected fields, safe spreadsheet text, correct counts, valid permissions | ✅ |
| Backups restorable; security and access-boundary tests pass | ✅ access tests; ⏳ restore drill is an operational step (see operations.md) |
