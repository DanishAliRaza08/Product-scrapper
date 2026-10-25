import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="mpi-test-"))
os.environ.setdefault("MPI_DATABASE_URL", "postgresql+psycopg://mpi:mpi@localhost:5432/mpi_test")
os.environ["MPI_CELERY_EAGER"] = "true"
os.environ["MPI_STORAGE_DIR"] = str(_tmp / "storage")
os.environ["MPI_SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["MPI_JOB_RETRY_BASE_SECONDS"] = "0.01"
os.environ["MPI_REDIS_URL"] = "redis://localhost:6399/0"  # unreachable on purpose: limiter uses memory
os.environ["MPI_JWT_SECRET"] = "test-secret-test-secret-test-secret"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.api.deps import login_limiter  # noqa: E402
from app.connectors import registry  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import reset_engine, session_scope  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def database():
    url = get_settings().database_url
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")
    reset_engine()
    yield


TRUNCATE = """
TRUNCATE organisations, users, refresh_tokens, marketplace_accounts, oauth_states, sync_checkpoints,
canonical_products, marketplace_listings, product_variations, product_images, product_sources, product_matches,
seller_orders, seller_order_items, research_projects, competitor_shops, project_products, saved_searches,
extraction_jobs, extraction_job_items, job_events, listing_observations, product_metrics, fee_profiles,
product_costs, exchange_rates, exports, alert_rules, notifications, audit_logs, connector_health
RESTART IDENTITY CASCADE
"""


@pytest.fixture(autouse=True)
def clean():
    with session_scope() as db:
        db.execute(text(TRUNCATE))
    login_limiter.reset()
    yield
    for platform in registry.platforms():
        registry.override_connector(platform, None)


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def register(client, email="owner@example.com", org="Acme Research", password="correct horse battery"):
    r = client.post("/api/v1/auth/register", json={"organisation_name": org, "email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(client):
    session = register(client)
    return {"session": session, "headers": auth(session["access_token"])}


def make_user(client, admin_headers, email, role, password="another strong pass"):
    r = client.post("/api/v1/users", headers=admin_headers, json={"email": email, "password": password, "role": role})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return auth(r.json()["access_token"])


def run_keyword_job(client, headers, keyword="yoga", limit=40, marketplace="DEMO_UK", **extra):
    body = {
        "marketplace_code": marketplace,
        "input_type": "keyword",
        "keyword": keyword,
        "result_limit": limit,
        **extra,
    }
    r = client.post("/api/v1/jobs", headers=headers, json=body)
    assert r.status_code == 202, r.text
    job = client.get(f"/api/v1/jobs/{r.json()['id']}", headers=headers).json()
    return job
