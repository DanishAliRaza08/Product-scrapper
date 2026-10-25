# Multi-Marketplace Product Intelligence Platform

Product research and catalogue intelligence across Amazon, eBay, TikTok Shop,
AliExpress and future marketplaces. It runs as a cloud web dashboard and an
installable Windows app, and both use one shared backend. Built from the
*Multi-Marketplace Product Intelligence Specification v1.0*. See
[docs/enhancements.md](docs/enhancements.md) for what was added beyond the spec.

> **Honesty by design.** Every metric is labelled **actual**, **estimated** or
> **not available**. Missing values stay blank and are never set to zero. Unknown
> costs never produce a profit figure. Connectors publish exactly what their
> source can and cannot provide.

| Layer | Technology | Location |
|---|---|---|
| API and business logic | Python 3.12, FastAPI, Pydantic, SQLAlchemy 2 | `backend/app` |
| Database | PostgreSQL 16 (partitioned history, FTS and trigram search, materialised view) | `backend/migrations` |
| Workers | Celery + Redis (Redis is broker only; PostgreSQL is the source of truth) | `backend/app/workers` |
| Web dashboard | Next.js 15 + TypeScript (static export) | `web/` |
| Windows app | Tauri 2 + the same React UI, OS-keychain token storage | `desktop/` |
| Ops | Docker Compose, GitHub Actions | `docker-compose.yml`, `.github/` |

## Quick start (Docker)

```bash
docker compose up --build                          # db, redis, migrate, api, worker, beat, web
docker compose exec api python -m scripts.seed_demo   # demo org with synthetic data
```

Open http://localhost:3000 and sign in as `demo@example.com` / `demo-password-change-me`.
The API docs (OpenAPI) are at http://localhost:8000/api/v1/docs.

## Local development (without Docker)

```bash
# PostgreSQL 16 and Redis 7 running locally
cd backend
python -m venv ../.venv && ../.venv/bin/pip install -e ".[dev]"
cp .env.example .env                     # adjust values
../.venv/bin/alembic upgrade head
../.venv/bin/python -m scripts.seed_demo
../.venv/bin/uvicorn app.main:app --reload --port 8000
../.venv/bin/celery -A app.workers.celery_app worker -Q jobs,exports,celery   # second terminal
../.venv/bin/celery -A app.workers.celery_app beat                            # third terminal (schedules)

cd ../web && npm install && npm run dev  # http://localhost:3000
```

Windows desktop app (needs Rust and the WebView2 runtime, which ships with Windows 10/11):

```bash
cd web && npm install && cd ../desktop && npm install
npx tauri dev     # development
npx tauri build   # signed NSIS/MSI installers in src-tauri/target/release/bundle
```

The desktop reads the API address from `MPI_API_URL`, or from
`%APPDATA%\com.productintelligence.desktop\config.json` (`{"api_url": "https://api.example.com"}`).

## Tests and checks

```bash
cd backend && ../.venv/bin/pytest -q && ../.venv/bin/ruff check .   # 77 tests against real PostgreSQL
cd web && npx tsc --noEmit && npm run build
cd desktop/src-tauri && cargo test
cd backend && python -m scripts.benchmark_scale --listings 1000000  # million-record benchmark
```

## Connecting real marketplaces

| Marketplace | Status | What you need |
|---|---|---|
| Demo (synthetic) | ready | nothing. Every record is clearly labelled `[DEMO]` |
| eBay | beta | eBay developer keys + RuName (`MPI_EBAY_*`) |
| Amazon | beta | SP-API app + LWA credentials (`MPI_AMAZON_*`) |
| TikTok Shop | planned | approved TikTok Shop Partner app |
| AliExpress | planned | approved AliExpress Open Platform app |

The full capability matrix is in [docs/connectors.md](docs/connectors.md). Confirm current API
access, regional support, data licences, quotas and costs before production use.

## Documentation

- [Architecture, data model and ERD](docs/architecture.md)
- [Connectors and capability matrix](docs/connectors.md)
- [API reference summary](docs/api.md)
- [Security checklist](docs/security.md)
- [Operations: deployment, scaling, monitoring, backup and recovery](docs/operations.md)
- [Testing, benchmark results and acceptance criteria](docs/testing.md)
- [Enhancements beyond the specification and known limitations](docs/enhancements.md)
