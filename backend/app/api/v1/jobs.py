from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require
from app.connectors.base import InputType
from app.core.db import get_db, session_scope
from app.core.errors import NotFound
from app.core.pagination import decode_cursor, encode_cursor
from app.core.security import Permission
from app.models import Job, JobEvent, JobItem, User
from app.schemas.common import ORM, Page
from app.services import jobs as job_service

router = APIRouter(tags=["jobs"])


class JobCreate(BaseModel):
    marketplace_code: str
    input_type: InputType
    url: str | None = Field(default=None, max_length=2048)
    keyword: str | None = Field(default=None, max_length=350)
    category: str | None = Field(default=None, max_length=200)
    listing_ids: list[str] | None = Field(default=None, max_length=1000)
    project_id: uuid.UUID | None = None
    result_limit: int = Field(default=1000, ge=1, le=100_000)
    filters: dict = Field(default_factory=dict)
    fields: list[str] | None = None
    refresh_mode: str = Field(default="manual", pattern="^(manual|daily|weekly)$")
    image_handling: str = Field(default="urls", pattern="^(urls|copy_when_permitted)$")


class JobOut(ORM):
    id: uuid.UUID
    job_type: str
    input_type: str
    marketplace_code: str | None = None
    input_url: str | None
    search_keyword: str | None
    category: str | None
    job_status: str
    project_id: uuid.UUID | None
    products_discovered: int
    products_processed: int
    products_failed: int
    products_skipped: int
    duplicates: int
    discovery_complete: bool
    source_limit_note: str | None
    error_summary: str | None
    progress_pct: float | None = None
    configuration: dict
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class JobItemOut(ORM):
    id: int
    external_product_id: str
    listing_id: int | None
    item_status: str
    attempt_count: int
    error_code: str | None
    error_message: str | None
    next_attempt_at: datetime | None
    processed_at: datetime | None


class JobEventOut(ORM):
    id: int
    event_type: str
    level: str
    message: str
    data: dict
    created_at: datetime


def job_out(job: Job) -> JobOut:
    out = JobOut.model_validate(job)
    out.marketplace_code = job.marketplace.code if job.marketplace else None
    done = job.products_processed + job.products_failed + job.products_skipped
    out.progress_pct = round(done / job.products_discovered * 100, 1) if job.products_discovered else None
    out.configuration = {k: v for k, v in (job.configuration or {}).items() if k != "validated"}
    return out


def _get_job(db: Session, user: User, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.organisation_id != user.organisation_id:
        raise NotFound("Job not found")
    return job


@router.post("/jobs", response_model=JobOut, status_code=202)
def create_job(
    body: JobCreate,
    user: User = Depends(require(Permission.RUN_JOBS)),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=128),
) -> JobOut:
    job = job_service.create_job(
        db,
        user,
        marketplace_code=body.marketplace_code,
        input_type=body.input_type.value,
        url=body.url,
        keyword=body.keyword,
        category=body.category,
        listing_ids=body.listing_ids,
        project_id=body.project_id,
        configuration={
            "result_limit": body.result_limit,
            "filters": body.filters,
            "fields": body.fields,
            "refresh_mode": body.refresh_mode,
            "image_handling": body.image_handling,
        },
        idempotency_key=idempotency_key,
    )
    is_new = job.job_status == "pending"
    db.commit()
    if is_new:
        job_service.enqueue(job.id)
    db.refresh(job)
    return job_out(job)


@router.get("/jobs", response_model=Page[JobOut])
def list_jobs(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    status: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> Page[JobOut]:
    stmt = select(Job).where(Job.organisation_id == user.organisation_id)
    if status:
        stmt = stmt.where(Job.job_status == status)
    c = decode_cursor(cursor)
    if c:
        stmt = stmt.where(Job.created_at < datetime.fromisoformat(c["t"]))
    rows = list(db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit + 1)))
    nxt = encode_cursor({"t": rows[limit - 1].created_at.isoformat()}) if len(rows) > limit else None
    return Page(items=[job_out(j) for j in rows[:limit]], next_cursor=nxt)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> JobOut:
    return job_out(_get_job(db, user, job_id))


@router.get("/jobs/{job_id}/items", response_model=Page[JobItemOut])
def job_items(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    status: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> Page[JobItemOut]:
    _get_job(db, user, job_id)
    stmt = select(JobItem).where(JobItem.job_id == job_id)
    if status:
        stmt = stmt.where(JobItem.item_status == status)
    c = decode_cursor(cursor)
    if c:
        stmt = stmt.where(JobItem.id > c["id"])
    rows = list(db.scalars(stmt.order_by(JobItem.id).limit(limit + 1)))
    nxt = encode_cursor({"id": rows[limit - 1].id}) if len(rows) > limit else None
    counts = dict(
        db.execute(
            select(JobItem.item_status, func.count()).where(JobItem.job_id == job_id).group_by(JobItem.item_status)
        ).all()
    )
    return Page(
        items=[JobItemOut.model_validate(r) for r in rows[:limit]],
        next_cursor=nxt,
        total=counts.get(status) if status else sum(counts.values()),
    )


@router.get("/jobs/{job_id}/events", response_model=list[JobEventOut])
def job_events(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    after_id: int = 0,
    limit: int = Query(default=200, ge=1, le=1000),
):
    _get_job(db, user, job_id)
    return db.scalars(
        select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > after_id).order_by(JobEvent.id).limit(limit)
    ).all()


@router.get("/jobs/{job_id}/stream")
async def job_stream(job_id: uuid.UUID, request: Request, user: User = Depends(current_user)) -> StreamingResponse:
    """Server-Sent Events: job progress snapshots plus new events. Clients that cannot
    use SSE poll GET /jobs/{id} and /jobs/{id}/events instead."""
    with session_scope() as db:
        _get_job(db, user, job_id)

    async def stream():
        last_event = 0
        while True:
            if await request.is_disconnected():
                return
            with session_scope() as db:
                job = db.get(Job, job_id)
                events = db.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.id > last_event)
                    .order_by(JobEvent.id)
                    .limit(100)
                ).all()
                snapshot = job_out(job).model_dump(mode="json") if job else None
                payload = [JobEventOut.model_validate(e).model_dump(mode="json") for e in events]
            for e in payload:
                last_event = e["id"]
                yield f"event: job_event\ndata: {json.dumps(e)}\n\n"
            yield f"event: progress\ndata: {json.dumps(snapshot)}\n\n"
            if snapshot is None or snapshot["job_status"] in job_service.TERMINAL | {"paused"}:
                return
            await asyncio.sleep(1.5)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, user: User = Depends(require(Permission.RUN_JOBS)), db: Session = Depends(get_db)):
    job = job_service.request_cancel(db, _get_job(db, user, job_id))
    db.commit()
    db.refresh(job)
    return job_out(job)


class ResumeIn(BaseModel):
    retry_failed: bool = True


@router.post("/jobs/{job_id}/resume", response_model=JobOut)
def resume_job(
    job_id: uuid.UUID,
    body: ResumeIn | None = None,
    user: User = Depends(require(Permission.RUN_JOBS)),
    db: Session = Depends(get_db),
):
    job = job_service.prepare_resume(db, _get_job(db, user, job_id), retry_failed=(body or ResumeIn()).retry_failed)
    db.commit()
    job_service.enqueue(job.id)
    db.refresh(job)
    return job_out(job)
