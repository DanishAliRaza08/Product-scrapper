"""Celery application. Redis is the broker only; job state lives in PostgreSQL."""

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.core.config import get_settings
from app.core.db import reset_engine

settings = get_settings()

celery_app = Celery("mpi", broker=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,  # a task lost with its worker is redelivered; the pipeline is idempotent
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_always_eager=settings.celery_eager,
    task_routes={
        "app.workers.tasks.run_job_task": {"queue": "jobs"},
        "app.workers.tasks.export_task": {"queue": "exports"},
    },
    broker_transport_options={"visibility_timeout": 6 * 3600},
    beat_schedule={
        "recover-stale-jobs": {"task": "app.workers.tasks.recover_stale_jobs_task", "schedule": 300.0},
        "schedule-refreshes": {"task": "app.workers.tasks.schedule_refreshes_task", "schedule": crontab(minute=15)},
        "observation-partitions": {
            "task": "app.workers.tasks.ensure_partitions_task",
            "schedule": crontab(hour=2, minute=5),
        },
        "expire-exports": {"task": "app.workers.tasks.expire_exports_task", "schedule": crontab(hour=3, minute=0)},
        "fx-rates": {"task": "app.workers.tasks.refresh_fx_task", "schedule": crontab(hour=16, minute=30)},
        "connector-health": {"task": "app.workers.tasks.connector_health_task", "schedule": 600.0},
    },
)


@worker_process_init.connect
def _reset_db_after_fork(**_):
    reset_engine()
