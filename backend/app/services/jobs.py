"""Extraction job lifecycle and the resumable discovery -> enrichment pipeline.

PostgreSQL is the durable source of truth: every discovered ID is persisted as a job
item before enrichment, the discovery cursor is checkpointed after each page, and
items are claimed with ``FOR UPDATE SKIP LOCKED`` so a crashed or restarted worker
resumes exactly where it stopped. Database transactions are never held open across
remote API calls.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.connectors.base import (
    AuthenticationFailed,
    ConnectorError,
    ConnectorInput,
    InputType,
    MarketplaceConnector,
    NormalisedListing,
    NotConfigured,
    PermissionDenied,
    ProductUnavailable,
    RateLimited,
    UnsupportedInput,
    ValidatedInput,
)
from app.connectors.http import backoff_delay
from app.connectors.registry import get_connector
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError, Conflict, NotFound
from app.models import Job, JobItem, Marketplace, ProjectProduct, ResearchProject, User
from app.services import alerts, matching
from app.services.events import emit
from app.services.ingest import ingest_listings

log = logging.getLogger(__name__)

TERMINAL = {"completed", "partial", "failed", "cancelled"}
RESUMABLE = {"paused", "failed", "cancelled", "partial"}
STALE_PROCESSING = timedelta(minutes=10)


def run_async(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------- creation
def create_job(
    db: Session,
    user: User,
    *,
    marketplace_code: str,
    input_type: str,
    url: str | None = None,
    keyword: str | None = None,
    category: str | None = None,
    listing_ids: list[str] | None = None,
    project_id: uuid.UUID | None = None,
    configuration: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    job_type: str = "research",
) -> Job:
    if idempotency_key:
        existing = db.scalar(
            select(Job).where(Job.organisation_id == user.organisation_id, Job.idempotency_key == idempotency_key)
        )
        if existing:
            return existing
    marketplace = db.scalar(select(Marketplace).where(Marketplace.code == marketplace_code))
    if marketplace is None or not marketplace.is_enabled:
        raise AppError(f"Unknown or disabled marketplace {marketplace_code}", code="unknown_marketplace")
    if project_id is not None:
        project = db.get(ResearchProject, project_id)
        if project is None or project.organisation_id != user.organisation_id:
            raise NotFound("Project not found")
    configuration = dict(configuration or {})
    configuration.setdefault("result_limit", 1000)
    configuration["result_limit"] = max(1, min(int(configuration["result_limit"]), 100_000))

    connector = get_connector(marketplace.platform)
    if not connector.is_configured():
        raise AppError(
            f"{connector.manifest.display_name} is not configured. "
            f"{connector.manifest.configuration_hint or ''}".strip(),
            code="connector_not_configured",
        )
    try:
        validated = run_async(
            connector.validate_input(
                ConnectorInput(
                    input_type=InputType(input_type),
                    marketplace_code=marketplace_code,
                    url=url,
                    keyword=keyword,
                    category=category,
                    listing_ids=listing_ids,
                    filters=configuration.get("filters") or {},
                )
            )
        )
    except (UnsupportedInput, NotConfigured) as exc:
        raise AppError(exc.message, code=exc.code) from exc
    except ValueError as exc:
        raise AppError(str(exc), code="invalid_input") from exc

    configuration["validated"] = validated.model_dump(mode="json")
    job = Job(
        organisation_id=user.organisation_id,
        user_id=user.id,
        project_id=project_id,
        marketplace_id=marketplace.id,
        job_type=job_type,
        input_type=input_type,
        input_url=validated.normalised_url,
        search_keyword=validated.keyword,
        category=validated.category_id,
        configuration=configuration,
        source_limit_note=validated.source_limit_note,
        idempotency_key=idempotency_key,
    )
    db.add(job)
    db.flush()
    emit(db, job.id, "created", f"Job created for {marketplace.name}", warnings=validated.warnings)
    return job


def enqueue(job_id: uuid.UUID) -> None:
    with session_scope() as db:
        db.execute(
            update(Job).where(Job.id == job_id, Job.job_status.in_(["pending", *RESUMABLE])).values(job_status="queued")
        )
    if get_settings().celery_eager:
        run_job(job_id)
        return
    from app.workers.tasks import run_job_task

    run_job_task.delay(str(job_id))


def request_cancel(db: Session, job: Job) -> Job:
    if job.job_status in TERMINAL:
        raise Conflict(f"Job is already {job.job_status}")
    if job.job_status in ("pending", "queued", "paused"):
        job.job_status = "cancelled"
        job.completed_at = datetime.now(UTC)
        emit(db, job.id, "cancelled", "Job cancelled before processing started")
    else:
        job.job_status = "cancelling"
        emit(db, job.id, "cancelling", "Cancellation requested; stopping after the current batch")
    return job


def prepare_resume(db: Session, job: Job, retry_failed: bool = True) -> Job:
    if job.job_status not in RESUMABLE:
        raise Conflict(f"Job in status {job.job_status} cannot be resumed")
    reset = update(JobItem).where(JobItem.job_id == job.id, JobItem.item_status == "processing")
    db.execute(reset.values(item_status="pending"))
    if retry_failed:
        result = db.execute(
            update(JobItem)
            .where(JobItem.job_id == job.id, JobItem.item_status == "failed")
            .values(item_status="pending", attempt_count=0, next_attempt_at=None)
        )
        if result.rowcount:
            job.products_failed = max(0, job.products_failed - result.rowcount)
    job.job_status = "queued"
    job.completed_at = None
    job.error_summary = None
    emit(db, job.id, "resumed", "Job resumed from its last checkpoint")
    return job


# ----------------------------------------------------------------- pipeline
def _job_status(job_id: uuid.UUID) -> str | None:
    with session_scope() as db:
        return db.scalar(select(Job.job_status).where(Job.id == job_id))


def _finish(job_id: uuid.UUID, status: str | None = None, error: str | None = None) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id, with_for_update=True)
        assert job is not None
        if status is None:
            if job.job_status == "cancelling":
                status = "cancelled"
            elif job.products_failed == 0:
                status = "completed"
            elif job.products_processed > 0:
                status = "partial"
            else:
                status = "failed"
        job.job_status = status
        job.error_summary = error or job.error_summary
        if status in TERMINAL:
            job.completed_at = datetime.now(UTC)
        emit(
            db,
            job_id,
            status,
            f"Job {status}: {job.products_processed} processed, {job.products_failed} failed, "
            f"{job.products_skipped} unavailable/skipped",
            level="error" if status == "failed" else "info",
        )


async def _discover(job_id: uuid.UUID, connector: MarketplaceConnector, validated: ValidatedInput) -> None:
    while True:
        with session_scope() as db:
            job = db.get(Job, job_id)
            assert job is not None
            if job.discovery_complete or job.job_status == "cancelling":
                return
            cursor = job.discovery_cursor
            limit = int(job.configuration.get("result_limit", 1000))
            discovered = job.products_discovered
        if discovered >= limit:
            with session_scope() as db:
                db.execute(update(Job).where(Job.id == job_id).values(discovery_complete=True))
                emit(db, job_id, "discovery_limit", f"Configured result limit of {limit} reached")
            return

        page = await connector.discover_products(validated, cursor)
        ids = list(dict.fromkeys(page.external_ids))[: max(0, limit - discovered)]
        with session_scope() as db:
            inserted = 0
            if ids:
                rows = db.execute(
                    pg_insert(JobItem.__table__)
                    .values([{"job_id": job_id, "external_product_id": i} for i in ids])
                    .on_conflict_do_nothing(index_elements=["job_id", "external_product_id"])
                    .returning(JobItem.__table__.c.id)
                ).all()
                inserted = len(rows)
            done = page.next_cursor is None or not page.external_ids
            values: dict[str, Any] = {
                "products_discovered": Job.products_discovered + inserted,
                "duplicates": Job.duplicates + (len(page.external_ids) - inserted),
                "discovery_cursor": page.next_cursor,
                "discovery_complete": done,
                "heartbeat_at": func.now(),
            }
            if page.note:
                values["source_limit_note"] = page.note
            db.execute(update(Job).where(Job.id == job_id).values(**values))
            emit(
                db,
                job_id,
                "discovery_page",
                f"Discovered {inserted} new products",
                total_estimate=page.total_estimate,
                source_limit_reached=page.source_limit_reached,
            )
        if done:
            return


def _claim_batch(job_id: uuid.UUID, size: int) -> tuple[list[tuple[int, str]], datetime | None]:
    """Claim up to ``size`` due items. Returns the batch and, if none are due yet, the
    earliest time a deferred retry becomes due."""
    with session_scope() as db:
        now = datetime.now(UTC)
        due = (
            select(JobItem.id)
            .where(
                JobItem.job_id == job_id,
                JobItem.item_status == "pending",
                (JobItem.next_attempt_at.is_(None)) | (JobItem.next_attempt_at <= now),
            )
            .order_by(JobItem.id)
            .limit(size)
            .with_for_update(skip_locked=True)
        )
        rows = db.execute(
            update(JobItem)
            .where(JobItem.id.in_(due.scalar_subquery()))
            .values(item_status="processing", attempt_count=JobItem.attempt_count + 1)
            .returning(JobItem.id, JobItem.external_product_id)
        ).all()
        if rows:
            return [(r.id, r.external_product_id) for r in rows], None
        next_due = db.scalar(
            select(func.min(JobItem.next_attempt_at)).where(JobItem.job_id == job_id, JobItem.item_status == "pending")
        )
        return [], next_due


async def _fetch(
    connector: MarketplaceConnector, marketplace_code: str, ids: list[tuple[int, str]], concurrency: int
) -> list[tuple[int, str, NormalisedListing | ConnectorError]]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(item_id: int, external_id: str):
        async with semaphore:
            try:
                return item_id, external_id, await connector.get_product_details(marketplace_code, external_id)
            except ConnectorError as exc:
                return item_id, external_id, exc
            except Exception as exc:  # unexpected mapping bug: record per item, keep the job alive
                log.exception("unexpected connector failure")
                return item_id, external_id, ConnectorError(f"Unexpected error: {type(exc).__name__}")

    return await asyncio.gather(*(one(i, e) for i, e in ids))


async def _enrich(job_id: uuid.UUID, connector: MarketplaceConnector, marketplace_code: str) -> None:
    settings = get_settings()
    concurrency = connector.manifest.rate_limit.max_concurrency
    while True:
        if _job_status(job_id) == "cancelling":
            return
        batch, next_due = _claim_batch(job_id, settings.job_detail_batch_size)
        if not batch:
            if next_due is None:
                return
            await asyncio.sleep(min(30.0, max(0.0, (next_due - datetime.now(UTC)).total_seconds())))
            continue

        results = await _fetch(connector, marketplace_code, batch, concurrency)
        successes = [(item_id, r) for item_id, _, r in results if isinstance(r, NormalisedListing)]
        failures = [(item_id, ext, r) for item_id, ext, r in results if isinstance(r, ConnectorError)]
        fatal: ConnectorError | None = None

        with session_scope() as db:
            job = db.get(Job, job_id)
            assert job is not None
            ingest = ingest_listings(
                db,
                organisation_id=job.organisation_id,
                listings=[r for _, r in successes],
                connector_key=connector.manifest.key,
                connector_version=connector.manifest.version,
                job_id=job_id,
            )
            now = datetime.now(UTC)
            for item_id, listing in successes:
                db.execute(
                    update(JobItem)
                    .where(JobItem.id == item_id)
                    .values(
                        item_status="succeeded",
                        listing_id=ingest.listing_ids[listing.external_listing_id],
                        processed_at=now,
                        error_code=None,
                        error_message=None,
                    )
                )
            failed = unavailable = 0
            attempts = dict(
                db.execute(
                    select(JobItem.id, JobItem.attempt_count).where(JobItem.id.in_([f[0] for f in failures] or [0]))
                ).all()
            )
            for item_id, _, exc in failures:
                values: dict[str, Any] = {
                    "error_code": exc.code,
                    "error_message": exc.message[:500],
                    "processed_at": now,
                }
                if isinstance(exc, ProductUnavailable):
                    values["item_status"] = "unavailable"
                    unavailable += 1
                elif exc.retryable and attempts.get(item_id, 0) < settings.job_max_item_attempts:
                    delay = (
                        exc.retry_after
                        if isinstance(exc, RateLimited) and exc.retry_after
                        else backoff_delay(attempts.get(item_id, 1), base=settings.job_retry_base_seconds, cap=120)
                    )
                    values.update(
                        item_status="pending", next_attempt_at=now + timedelta(seconds=delay), processed_at=None
                    )
                elif isinstance(exc, (AuthenticationFailed, PermissionDenied, NotConfigured)):
                    values["item_status"] = "pending"  # keep for resume once access is fixed
                    fatal = exc
                else:
                    values["item_status"] = "failed"
                    failed += 1
                db.execute(update(JobItem).where(JobItem.id == item_id).values(**values))
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    products_processed=Job.products_processed + len(successes),
                    products_failed=Job.products_failed + failed,
                    products_skipped=Job.products_skipped + unavailable,
                    heartbeat_at=func.now(),
                )
            )
            if job.project_id and ingest.listing_ids:
                db.execute(
                    pg_insert(ProjectProduct.__table__)
                    .values(
                        [
                            {"project_id": job.project_id, "listing_id": lid, "added_by": job.user_id}
                            for lid in ingest.listing_ids.values()
                        ]
                    )
                    .on_conflict_do_nothing()
                )
            alerts.evaluate(db, job.organisation_id, ingest.changes)
            matching.auto_match_by_gtin(db, job.organisation_id, list(ingest.listing_ids.values()))
            emit(
                db,
                job_id,
                "batch",
                f"Processed batch: {len(successes)} ok, {failed} failed, {unavailable} unavailable",
                inserted=ingest.inserted,
                updated=ingest.updated,
            )
        if fatal is not None:
            raise fatal


async def _run(job_id: uuid.UUID, connector: MarketplaceConnector, validated: ValidatedInput) -> None:
    try:
        await _discover(job_id, connector, validated)
        await _enrich(job_id, connector, validated.marketplace_code)
    finally:
        await connector.aclose()


def run_job(job_id: uuid.UUID) -> str:
    with session_scope() as db:
        job = db.get(Job, job_id, with_for_update=True)
        if job is None:
            raise NotFound("Job not found")
        if job.job_status in TERMINAL or job.job_status == "running":
            return job.job_status
        job.job_status = "running"
        job.started_at = job.started_at or datetime.now(UTC)
        job.heartbeat_at = datetime.now(UTC)
        # Items left 'processing' by a crashed worker are safe to retry.
        db.execute(
            update(JobItem)
            .where(JobItem.job_id == job_id, JobItem.item_status == "processing")
            .values(item_status="pending")
        )
        job_type = job.job_type
        marketplace = db.get(Marketplace, job.marketplace_id)
        assert marketplace is not None
        validated = (
            ValidatedInput.model_validate(job.configuration["validated"]) if job_type != "account_sync" else None
        )
        emit(db, job_id, "running", "Job started")
    try:
        if job_type == "account_sync":
            from app.services.sync import run_account_sync

            run_async(run_account_sync(job_id))
        else:
            assert validated is not None
            run_async(_run(job_id, get_connector(marketplace.platform), validated))
    except (AuthenticationFailed, PermissionDenied, NotConfigured) as exc:
        _finish(job_id, "paused", f"{exc.message} Fix access, then resume the job.")
        return "paused"
    except PermissionError as exc:
        _finish(job_id, "paused", f"{exc} Reconnect the account, then resume.")
        return "paused"
    except ConnectorError as exc:
        _finish(job_id, "failed", exc.message)
        return "failed"
    except Exception as exc:
        log.exception("job crashed", extra={"job_id": str(job_id)})
        _finish(job_id, "failed", f"Internal error: {type(exc).__name__}")
        raise
    _finish(job_id)
    return _job_status(job_id) or "unknown"


def recover_stale_jobs(stale_after: timedelta = STALE_PROCESSING) -> list[uuid.UUID]:
    """Requeue jobs whose worker stopped heart-beating (crash, deploy, OOM)."""
    cutoff = datetime.now(UTC) - stale_after
    with session_scope() as db:
        ids = list(
            db.scalars(
                select(Job.id)
                .where(Job.job_status == "running", Job.heartbeat_at < cutoff)
                .with_for_update(skip_locked=True)
            )
        )
        for job_id in ids:
            db.execute(update(Job).where(Job.id == job_id).values(job_status="queued"))
            emit(db, job_id, "recovered", "Worker heartbeat lost; job re-queued from checkpoint", level="warning")
    return ids
