import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.connectors.registry import get_connector, platforms
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import ConnectorHealth
from app.services import exports, fx, jobs, refresh
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, max_retries=0)
def run_job_task(self, job_id: str) -> str:
    return jobs.run_job(uuid.UUID(job_id))


@celery_app.task
def export_task(export_id: str) -> None:
    exports.generate(uuid.UUID(export_id))


@celery_app.task
def recover_stale_jobs_task() -> int:
    ids = jobs.recover_stale_jobs()
    for job_id in ids:
        jobs.enqueue(job_id)
    return len(ids)


@celery_app.task
def schedule_refreshes_task() -> int:
    return len(refresh.schedule_refreshes())


@celery_app.task
def ensure_partitions_task() -> int:
    with session_scope() as db:
        return db.execute(
            text("SELECT ensure_observation_partitions(:m)"), {"m": get_settings().observation_partition_months_ahead}
        ).scalar_one()


@celery_app.task
def expire_exports_task() -> int:
    return exports.expire_old_exports()


@celery_app.task
def refresh_fx_task() -> int:
    with session_scope() as db:
        return fx.refresh_ecb_rates(db)


@celery_app.task
def connector_health_task() -> dict:
    results = {}
    for platform in platforms():
        connector = get_connector(platform)
        if not connector.is_configured():
            status = "not_configured"
        else:
            try:
                status = "healthy" if asyncio.run(connector.health_check()) else "degraded"
            except Exception:
                status = "down"
        results[platform] = status
        now = datetime.now(UTC)
        with session_scope() as db:
            stmt = pg_insert(ConnectorHealth.__table__).values(
                connector=platform,
                status=status,
                updated_at=now,
                last_success_at=now if status == "healthy" else None,
                last_failure_at=now if status == "down" else None,
                consecutive_failures=1 if status == "down" else 0,
            )
            db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["connector"],
                    set_={
                        "status": stmt.excluded.status,
                        "updated_at": now,
                        "last_success_at": text("COALESCE(EXCLUDED.last_success_at, connector_health.last_success_at)"),
                        "last_failure_at": text("COALESCE(EXCLUDED.last_failure_at, connector_health.last_failure_at)"),
                        "consecutive_failures": text(
                            "CASE WHEN EXCLUDED.status = 'down' "
                            "THEN connector_health.consecutive_failures + 1 ELSE 0 END"
                        ),
                    },
                )
            )
    return results
