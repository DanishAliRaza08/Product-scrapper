"""Scheduled refresh: pick the listings most worth re-checking and create refresh jobs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select

from app.connectors.base import InputType
from app.connectors.registry import get_connector
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Job, Listing, Marketplace, User
from app.services import jobs

MAX_PER_JOB = 500


def due_listings(db, organisation_id: uuid.UUID, marketplace_id: int, limit: int) -> list[str]:
    """Priority: watched first, then volatile (recently changed) and commercially
    important (better rank), then the stalest."""
    stale_before = datetime.now(UTC) - timedelta(hours=get_settings().refresh_stale_after_hours)
    recently_changed = case((Listing.last_changed_at >= datetime.now(UTC) - timedelta(days=7), 1), else_=0)
    return list(
        db.scalars(
            select(Listing.external_listing_id)
            .where(
                Listing.organisation_id == organisation_id,
                Listing.marketplace_id == marketplace_id,
                Listing.source_type == "competitor_research",
                (Listing.last_seen_at < stale_before)
                | (Listing.is_watched.is_(True) & (Listing.last_seen_at < datetime.now(UTC) - timedelta(hours=6))),
            )
            .order_by(
                Listing.is_watched.desc(),
                recently_changed.desc(),
                Listing.refresh_priority.desc(),
                func.coalesce(Listing.sales_rank, 10**9),
                Listing.last_seen_at,
            )
            .limit(limit)
        )
    )


def schedule_refreshes() -> list[uuid.UUID]:
    created: list[uuid.UUID] = []
    with session_scope() as db:
        groups = db.execute(
            select(Listing.organisation_id, Listing.marketplace_id, Marketplace.code, Marketplace.platform)
            .join(Marketplace, Marketplace.id == Listing.marketplace_id)
            .where(Listing.source_type == "competitor_research")
            .group_by(Listing.organisation_id, Listing.marketplace_id, Marketplace.code, Marketplace.platform)
        ).all()
        for org_id, marketplace_id, code, platform in groups:
            if not get_connector(platform).is_configured():
                continue
            busy = db.scalar(
                select(func.count()).where(
                    Job.organisation_id == org_id,
                    Job.marketplace_id == marketplace_id,
                    Job.job_type == "refresh",
                    Job.job_status.in_(["pending", "queued", "running"]),
                )
            )
            if busy:
                continue  # one refresh in flight per org+marketplace respects source quotas
            ids = due_listings(db, org_id, marketplace_id, MAX_PER_JOB)
            if not ids:
                continue
            owner = db.scalar(
                select(User)
                .where(User.organisation_id == org_id, User.role == "admin", User.is_active.is_(True))
                .limit(1)
            )
            if owner is None:
                continue
            job = jobs.create_job(
                db,
                owner,
                marketplace_code=code,
                input_type=InputType.LISTING_IDS.value,
                listing_ids=ids,
                job_type="refresh",
                configuration={"result_limit": len(ids)},
            )
            created.append(job.id)
    for job_id in created:
        jobs.enqueue(job_id)
    return created
