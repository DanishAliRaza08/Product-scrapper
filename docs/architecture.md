# Architecture

## Logical architecture

```mermaid
flowchart LR
  subgraph Clients
    D[Windows app<br/>Tauri 2 + React] -->|HTTPS, Bearer JWT| API
    W[Web dashboard<br/>Next.js static] -->|HTTPS, Bearer JWT + httpOnly refresh cookie| API
  end
  API[FastAPI API<br/>auth, RBAC, validation] --> PG[(PostgreSQL<br/>system of record)]
  API -->|enqueue| R[(Redis<br/>broker + rate limits)]
  R --> WK[Celery workers<br/>jobs, exports, sync]
  B[Celery beat<br/>schedules] --> R
  WK --> PG
  WK --> C[Connectors<br/>demo / eBay / Amazon / ...]
  C -->|official APIs only| MP[(Marketplace APIs)]
  WK --> S3[(Object storage<br/>exports, permitted images)]
  API --> S3
  WK --> SEC[(Secrets store<br/>seller credentials)]
```

* The desktop and web clients share **one UI build**: Next.js is exported to static
  files, served by the web host and bundled by Tauri. Desktop-only features
  (keychain, native downloads and notifications) go through `web/lib/desktop.ts`, and
  each one is a no-op in the browser.
* Neither client connects to the database. Background jobs run in the cloud and
  keep running after the Windows app is closed.
* Connector credentials live in the secrets store. SQL holds only `credential_reference`.

## Extraction pipeline

```mermaid
sequenceDiagram
  participant U as User
  participant A as API
  participant DB as PostgreSQL
  participant Q as Redis/Celery
  participant W as Worker
  participant C as Connector
  U->>A: POST /jobs (Idempotency-Key)
  A->>C: validate_input (URL host allow-list, identifiers)
  A->>DB: insert job + validated config
  A->>Q: enqueue
  Q->>W: run_job
  loop discovery pages
    W->>C: discover_products(cursor)
    W->>DB: insert job items ON CONFLICT DO NOTHING, checkpoint cursor
  end
  loop batches (FOR UPDATE SKIP LOCKED)
    W->>DB: claim pending items
    W->>C: get_product_details (bounded concurrency, rate limit, retries)
    W->>DB: bulk upsert listings, variations, images, provenance, observations, metrics
    W->>DB: item outcomes, counters, alerts, GTIN matches, heartbeat
  end
  W->>DB: completed / partial / failed / paused
```

Reliability properties:

* Every discovered ID is stored before enrichment. The discovery cursor is checkpointed per page.
* Item claiming uses `SKIP LOCKED`. Items left in `processing` by a crashed worker are
  released on restart. The `recover_stale_jobs` task requeues jobs whose heartbeat stopped.
* Transient errors retry with full-jitter exponential backoff and honour `Retry-After`.
  A circuit breaker stops calls to a source that keeps failing. Items that keep failing
  become `failed` (the dead-letter set) and can be retried with *Resume*.
* Authentication or permission errors **pause** the job instead of failing it. Fix access, then resume.
* No database transaction is held open across a remote API call.

## Data model

```mermaid
erDiagram
  organisations ||--o{ users : has
  organisations ||--o{ marketplace_accounts : connects
  organisations ||--o{ research_projects : owns
  organisations ||--o{ marketplace_listings : "tenant-scoped"
  marketplaces ||--o{ marketplace_listings : lists
  canonical_products ||--o{ marketplace_listings : "links (after review)"
  marketplace_listings ||--o{ product_variations : has
  marketplace_listings ||--o{ product_images : has
  marketplace_listings ||--o{ product_sources : "field provenance"
  marketplace_listings ||--o{ listing_observations : "history (partitioned)"
  marketplace_listings ||--o{ product_metrics : "actual/estimated/unavailable"
  marketplace_listings ||--o{ product_costs : "time-stamped inputs"
  marketplace_listings ||--o{ product_matches : candidate
  marketplace_accounts ||--o{ seller_orders : syncs
  seller_orders ||--o{ seller_order_items : contains
  marketplace_accounts ||--o{ sync_checkpoints : "per stream"
  research_projects ||--o{ project_products : groups
  research_projects ||--o{ competitor_shops : tracks
  extraction_jobs ||--o{ extraction_job_items : "per-product outcome"
  extraction_jobs ||--o{ job_events : timeline
  fee_profiles ||--o{ product_costs : "fees"
```

The schema is owned by SQL migrations (`backend/migrations/sql`). Key decisions:

| Decision | Why |
|---|---|
| Listings are scoped to an organisation (`UNIQUE (organisation_id, marketplace_id, external_listing_id)`) | Own-account data can never leak between tenants. Research is attributable. |
| Canonical products are separate from listings | One product, many listings, each with independent price, stock, images and variations. |
| `listing_observations` is partitioned by month and append-only | Hundreds of millions of rows. Old months can be detached or archived. |
| Observations are written only on change, plus at most one per day | Keeps history dense where it matters and small where it doesn't. |
| `product_metrics.measurement_type` uses a CHECK so `unavailable` means `numeric_value IS NULL` | Zero can never stand in for unknown. |
| Money is `NUMERIC(14,4)` and always carries its currency | No float rounding, and no amount without a currency. |
| `product_costs` is append-only with `effective_from` | Historical profit calculations stay reproducible. |
| `mv_listing_summary` materialised view | Dashboards for large organisations read pre-aggregated data. |

## Indexing for a million or more records

* Keyset pagination with row-value comparisons `(column, id) > (:v, :id)`, served by
  `(organisation_id, column, id)` indexes. Deep pages cost the same as the first page.
* A generated `tsvector` column with GIN (full-text), plus trigram GIN on title and category.
* A partial index on pending job items, and a partial index for refresh scheduling.
