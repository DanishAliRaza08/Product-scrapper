import uuid

from sqlalchemy import func, select, text

from app.connectors import registry
from app.connectors.demo import DemoConnector
from app.core.db import session_scope
from app.models import Job, JobItem, Listing, Observation
from app.services import jobs as job_service
from tests.conftest import run_keyword_job


def test_keyword_job_end_to_end(client, admin):
    job = run_keyword_job(client, admin["headers"], keyword="yoga", limit=40)
    assert job["job_status"] in ("completed", "partial"), job
    assert job["products_discovered"] == 40
    assert job["products_processed"] + job["products_skipped"] + job["products_failed"] == 40
    assert job["source_limit_note"]
    assert job["progress_pct"] == 100.0
    items = client.get(f"/api/v1/jobs/{job['id']}/items", headers=admin["headers"]).json()
    assert items["total"] == 40
    events = client.get(f"/api/v1/jobs/{job['id']}/events", headers=admin["headers"]).json()
    assert events[0]["event_type"] == "created" and events[-1]["event_type"] in ("completed", "partial")
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Listing)) == job["products_processed"]
        assert db.scalar(select(func.count()).select_from(Observation)) == job["products_processed"]


def test_rerun_is_idempotent_and_records_no_duplicate_history(client, admin):
    run_keyword_job(client, admin["headers"], keyword="yoga", limit=20)
    run_keyword_job(client, admin["headers"], keyword="yoga", limit=20)
    with session_scope() as db:
        listings = db.scalar(select(func.count()).select_from(Listing))
        observations = db.scalar(select(func.count()).select_from(Observation))
        dupes = db.scalar(
            select(func.count()).select_from(
                select(Listing.external_listing_id)
                .group_by(Listing.marketplace_id, Listing.external_listing_id)
                .having(func.count() > 1)
                .subquery()
            )
        )
    assert dupes == 0
    assert observations == listings  # unchanged values on the same day add no observation


def test_idempotency_key_returns_same_job(client, admin):
    body = {"marketplace_code": "DEMO_US", "input_type": "keyword", "keyword": "charger", "result_limit": 5}
    h = {**admin["headers"], "Idempotency-Key": "abc-123"}
    a = client.post("/api/v1/jobs", headers=h, json=body).json()
    b = client.post("/api/v1/jobs", headers=h, json=body).json()
    assert a["id"] == b["id"]


def test_input_validation_errors(client, admin):
    h = admin["headers"]
    r = client.post(
        "/api/v1/jobs",
        headers=h,
        json={"marketplace_code": "DEMO_UK", "input_type": "shop_url", "url": "http://169.254.169.254/latest"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_input"
    r = client.post(
        "/api/v1/jobs", headers=h, json={"marketplace_code": "EBAY_UK", "input_type": "keyword", "keyword": "x"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "connector_not_configured"
    r = client.post(
        "/api/v1/jobs", headers=h, json={"marketplace_code": "NOPE", "input_type": "keyword", "keyword": "x"}
    )
    assert r.status_code == 400


def test_shop_and_product_url_jobs(client, admin):
    h = admin["headers"]
    r = client.post(
        "/api/v1/jobs",
        headers=h,
        json={
            "marketplace_code": "DEMO_US",
            "input_type": "shop_url",
            "url": "https://demo-marketplace.example/shop/seller7",
            "result_limit": 15,
        },
    )
    assert r.status_code == 202, r.text
    r = client.post(
        "/api/v1/jobs",
        headers=h,
        json={"marketplace_code": "DEMO_US", "input_type": "product_url", "url": "demo-marketplace.example/p/DEMO-42"},
    )
    job = client.get(f"/api/v1/jobs/{r.json()['id']}", headers=h).json()
    assert job["products_discovered"] == 1 and job["job_status"] == "completed"


def test_transient_failures_are_retried(client, admin):
    from app.connectors.base import ConnectorInput, InputType
    from app.services.jobs import run_async

    d = DemoConnector()
    v = run_async(
        d.validate_input(ConnectorInput(input_type=InputType.KEYWORD, marketplace_code="DEMO_UK", keyword="yoga"))
    )
    first_ids = run_async(d.discover_products(v)).external_ids[:5]
    registry.override_connector("demo", DemoConnector(failure_ids=set(first_ids)))
    job = run_keyword_job(client, admin["headers"], keyword="yoga", limit=10)
    assert job["products_failed"] == 0
    with session_scope() as db:
        attempts = dict(
            db.execute(
                select(JobItem.external_product_id, JobItem.attempt_count).where(JobItem.job_id == uuid.UUID(job["id"]))
            ).all()
        )
    assert all(attempts[i] == 2 for i in first_ids if i in attempts)


def test_permanent_failures_make_job_partial_and_resumable(client, admin):
    class Broken(DemoConnector):
        async def get_product_details(self, marketplace_code, external_product_id):
            from app.connectors.base import ConnectorError

            if external_product_id.endswith(("1", "3")):
                raise ConnectorError("mapping failed")
            return await super().get_product_details(marketplace_code, external_product_id)

    registry.override_connector("demo", Broken())
    job = run_keyword_job(client, admin["headers"], keyword="mat", limit=30)
    assert job["job_status"] == "partial", job
    assert job["products_failed"] > 0
    failed = client.get(f"/api/v1/jobs/{job['id']}/items?status=failed", headers=admin["headers"]).json()
    assert failed["items"][0]["error_message"] == "mapping failed"

    registry.override_connector("demo", None)
    r = client.post(f"/api/v1/jobs/{job['id']}/resume", headers=admin["headers"], json={"retry_failed": True})
    assert r.status_code == 200, r.text
    resumed = client.get(f"/api/v1/jobs/{job['id']}", headers=admin["headers"]).json()
    assert resumed["job_status"] == "completed" and resumed["products_failed"] == 0


def test_crash_recovery_resumes_from_checkpoint(client, admin):
    h = admin["headers"]
    job = run_keyword_job(client, h, keyword="bands", limit=30)
    job_id = uuid.UUID(job["id"])
    # Simulate a worker dying mid-run: some items stuck 'processing', job still 'running'.
    with session_scope() as db:
        db.execute(
            text(
                "UPDATE extraction_job_items SET item_status='processing' WHERE id IN "
                "(SELECT id FROM extraction_job_items WHERE job_id=:j ORDER BY id LIMIT 7)"
            ),
            {"j": job_id},
        )
        processed = db.scalar(select(Job.products_processed).where(Job.id == job_id))
        db.execute(
            text(
                "UPDATE extraction_jobs SET job_status='running', products_processed=:p, "
                "heartbeat_at=now() - interval '1 hour' WHERE id=:j"
            ),
            {"j": job_id, "p": processed - 7},
        )
    recovered = job_service.recover_stale_jobs()
    assert recovered == [job_id]
    job_service.enqueue(job_id)
    final = client.get(f"/api/v1/jobs/{job_id}", headers=h).json()
    assert final["job_status"] in ("completed", "partial")
    assert final["products_processed"] == processed


def test_cancel(client, admin):
    h = admin["headers"]
    with session_scope():
        pass
    from app.core.config import get_settings

    settings = get_settings()
    settings.celery_eager = False  # create without running
    try:
        import app.workers.tasks as tasks

        original = tasks.run_job_task.delay
        tasks.run_job_task.delay = lambda *_: None
        r = client.post(
            "/api/v1/jobs",
            headers=h,
            json={"marketplace_code": "DEMO_UK", "input_type": "keyword", "keyword": "pan", "result_limit": 5},
        )
        job_id = r.json()["id"]
        c = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=h)
        assert c.json()["job_status"] == "cancelled"
        assert client.post(f"/api/v1/jobs/{job_id}/cancel", headers=h).status_code == 409
    finally:
        settings.celery_eager = True
        tasks.run_job_task.delay = original


def test_job_attaches_products_to_project(client, admin):
    h = admin["headers"]
    p = client.post("/api/v1/projects", headers=h, json={"name": "Fitness UK"}).json()
    run_keyword_job(client, h, keyword="rope", limit=8, project_id=p["id"])
    projects = client.get("/api/v1/projects", headers=h).json()
    assert projects[0]["product_count"] > 0
    items = client.get(f"/api/v1/products?project_id={p['id']}", headers=h).json()["items"]
    assert len(items) == projects[0]["product_count"]
