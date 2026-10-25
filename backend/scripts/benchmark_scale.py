"""Million-record benchmark (acceptance criterion: import and query >= 1,000,000
synthetic listings in a representative environment).

Usage:
    python -m scripts.benchmark_scale --listings 1000000 --observations-per-listing 3

1. Bulk-loads synthetic listings server-side with generate_series (fast path, as a
   bulk import would use COPY), plus observation history.
2. Measures the application ingest path (ingest_listings, batched upserts) separately.
3. Times representative API queries via the same query builders the API uses.
All rows belong to a dedicated "Benchmark" organisation and are clearly synthetic.
"""

from __future__ import annotations

import argparse
import statistics
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text

from app.connectors.base import MeasurementType, MetricValue, NormalisedListing, StockStatus
from app.core.db import session_scope
from app.models import Organisation
from app.services import analytics
from app.services.ingest import ingest_listings
from app.services.products import ProductFilters, build_query, estimate_count, paginate


def timed(label: str, fn, repeat: int = 5) -> float:
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    p50 = statistics.median(samples)
    print(f"  {label:<48} p50 {p50:8.1f} ms   max {max(samples):8.1f} ms")
    return p50


def bulk_load(org_id: uuid.UUID, n: int, obs_per_listing: int) -> None:
    with session_scope() as db:
        mp = dict(db.execute(text("SELECT code, id FROM marketplaces WHERE code IN ('DEMO_US','DEMO_UK')")).all())
        start = time.perf_counter()
        db.execute(
            text("""
            INSERT INTO marketplace_listings (organisation_id, marketplace_id, external_listing_id, title, brand,
                category_path, product_url, currency_code, current_price, stock_status, rating, review_count,
                sales_rank, source_type, gtin, content_hash, last_seen_at)
            SELECT :org, CASE WHEN g % 2 = 0 THEN :us ELSE :uk END, 'BENCH-' || g,
                   '[SYNTHETIC] ' || (ARRAY['Yoga Mat','Chef Knife','USB-C Charger','Dog Lead','Storage Basket',
                        'Resistance Bands','Phone Stand','Cast Iron Skillet'])[1 + g % 8] || ' model ' || g,
                   (ARRAY['Northwind','Contoso','Fabrikam','Tailspin'])[1 + g % 4],
                   (ARRAY['Sports > Fitness','Kitchen > Cookware','Electronics > Accessories',
                          'Home > Storage'])[1 + g % 4],
                   'https://demo-marketplace.example/p/BENCH-' || g,
                   CASE WHEN g % 2 = 0 THEN 'USD' ELSE 'GBP' END,
                   round((5 + (g * 7919 % 20000) / 100.0)::numeric, 2),
                   (ARRAY['in_stock','in_stock','in_stock','low_stock','out_of_stock'])[1 + g % 5],
                   round((3 + (g % 20) / 10.0)::numeric, 1), g % 5000, 1 + (g * 104729 % 900000),
                   'competitor_research', NULL, md5(g::text), now() - (g % 30) * interval '1 day'
            FROM generate_series(1::bigint, :n) AS g
            ON CONFLICT DO NOTHING
            """),
            {"org": org_id, "us": mp["DEMO_US"], "uk": mp["DEMO_UK"], "n": n},
        )
        print(f"  bulk insert {n:,} listings: {time.perf_counter() - start:.1f}s")
    if obs_per_listing:
        start = time.perf_counter()
        with session_scope() as db:
            db.execute(
                text("""
                INSERT INTO listing_observations
                    (listing_id, observed_at, price, currency_code, stock_status, sales_rank)
                SELECT l.id, now() - (k * interval '9 days') - (l.id % 86400) * interval '1 second',
                       l.current_price * (1 + (k - 1) * 0.02), l.currency_code, l.stock_status, l.sales_rank + k * 10
                FROM marketplace_listings l CROSS JOIN generate_series(0, :k - 1) AS k
                WHERE l.organisation_id = :org
                """),
                {"org": org_id, "k": obs_per_listing},
            )
        print(f"  bulk insert {n * obs_per_listing:,} observations: {time.perf_counter() - start:.1f}s")
    with session_scope() as db:
        db.execute(text("ANALYZE marketplace_listings; ANALYZE listing_observations;"))
    start = time.perf_counter()
    with session_scope() as db:
        analytics.refresh_summary_view(db)
    print(f"  refresh dashboard materialised view: {time.perf_counter() - start:.1f}s")


def ingest_path(org_id: uuid.UUID, n: int, batch: int = 500) -> None:
    now = datetime.now(UTC)
    start = time.perf_counter()
    for offset in range(0, n, batch):
        listings = [
            NormalisedListing(
                external_listing_id=f"INGEST-{i}",
                marketplace_code="DEMO_UK",
                title=f"[SYNTHETIC] Ingest item {i}",
                product_url=f"https://demo-marketplace.example/p/INGEST-{i}",
                currency_code="GBP",
                current_price=Decimal("9.99") + i % 50,
                stock_status=StockStatus.IN_STOCK,
                sales_rank=i,
                metrics=[
                    MetricValue(
                        name="competitor_sales", measurement_type=MeasurementType.UNAVAILABLE, source="benchmark"
                    )
                ],
                retrieved_at=now,
            )
            for i in range(offset, min(n, offset + batch))
        ]
        with session_scope() as db:
            ingest_listings(
                db, organisation_id=org_id, listings=listings, connector_key="benchmark", connector_version="1"
            )
    elapsed = time.perf_counter() - start
    print(f"  application ingest path: {n:,} listings in {elapsed:.1f}s ({n / elapsed:,.0f} listings/s)")


def queries(org_id: uuid.UUID) -> None:
    def search(filters: ProductFilters, cursor=None):
        with session_scope() as db:
            stmt = build_query(org_id, filters, can_view_own=True)
            return paginate(db, stmt, filters.sort, cursor, 50)

    timed("first page, newest updated", lambda: search(ProductFilters()))
    timed("full-text search 'yoga mat'", lambda: search(ProductFilters(q="yoga mat")))
    timed(
        "price 20-40 GBP, in stock, sort price",
        lambda: search(
            ProductFilters(
                price_min=Decimal(20),
                price_max=Decimal(40),
                currency="GBP",
                stock_status=["in_stock"],
                sort="price_asc",
            )
        ),
    )
    _, cursor = search(ProductFilters(sort="price_asc"))
    for _ in range(20):
        _, cursor = search(ProductFilters(sort="price_asc"), cursor)
    timed("deep keyset page (page 21, price sort)", lambda: search(ProductFilters(sort="price_asc"), cursor))
    timed("category + brand filter", lambda: search(ProductFilters(category="Kitchen", brand="Contoso")))

    def count():
        with session_scope() as db:
            return estimate_count(db, build_query(org_id, ProductFilters(q="charger"), can_view_own=True))

    timed("capped count (charger)", count)

    with session_scope() as db:
        lid = db.scalar(text("SELECT max(id) FROM marketplace_listings WHERE organisation_id=:o"), {"o": org_id})

    def history():
        with session_scope() as db:
            return db.execute(
                text("SELECT * FROM listing_observations WHERE listing_id=:l ORDER BY observed_at DESC LIMIT 100"),
                {"l": lid},
            ).all()

    timed("price history for one listing", history)
    timed("dashboard overview (materialised view)", lambda: _overview(org_id))


def _overview(org_id):
    with session_scope() as db:
        return analytics.overview(db, org_id, include_own=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listings", type=int, default=1_000_000)
    parser.add_argument("--observations-per-listing", type=int, default=3)
    parser.add_argument("--ingest-sample", type=int, default=20_000)
    parser.add_argument("--keep", action="store_true", help="keep the benchmark organisation afterwards")
    args = parser.parse_args()

    with session_scope() as db:
        org = db.scalar(select(Organisation).where(Organisation.name == "Benchmark (synthetic)"))
        if org is None:
            org = Organisation(name="Benchmark (synthetic)")
            db.add(org)
            db.flush()
        org_id = org.id
    print(f"Benchmark organisation {org_id}")
    print("Loading data…")
    bulk_load(org_id, args.listings, args.observations_per_listing)
    if args.ingest_sample:
        ingest_path(org_id, args.ingest_sample)
    with session_scope() as db:
        total = db.scalar(text("SELECT count(*) FROM marketplace_listings WHERE organisation_id=:o"), {"o": org_id})
        size = db.scalar(text("SELECT pg_size_pretty(pg_total_relation_size('marketplace_listings'))"))
    print(f"Listings for benchmark org: {total:,} (table incl. indexes: {size})")
    print("Query timings:")
    queries(org_id)
    if not args.keep:
        with session_scope() as db:
            db.execute(
                text(
                    "DELETE FROM listing_observations WHERE listing_id IN "
                    "(SELECT id FROM marketplace_listings WHERE organisation_id=:o)"
                ),
                {"o": org_id},
            )
            db.execute(text("DELETE FROM organisations WHERE id=:o"), {"o": org_id})
        print("Benchmark data removed (use --keep to retain it).")


if __name__ == "__main__":
    main()
