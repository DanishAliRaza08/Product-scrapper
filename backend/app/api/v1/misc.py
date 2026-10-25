"""Marketplaces, connector manifests, notifications, alert rules, audit log, health."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, text, update
from sqlalchemy.orm import Session

from app.api.deps import current_user, require
from app.connectors import registry
from app.connectors.base import CapabilityManifest
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.core.security import Permission
from app.models import AlertRule, AuditLog, ConnectorHealth, Listing, Marketplace, Notification, ResearchProject, User
from app.schemas.common import ORM, MarketplaceOut

router = APIRouter()


@router.get("/marketplaces", response_model=list[MarketplaceOut], tags=["reference"])
def marketplaces(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Marketplace).where(Marketplace.is_enabled.is_(True)).order_by(Marketplace.id)).all()


class ConnectorOut(CapabilityManifest):
    health: str | None = None
    health_updated_at: datetime | None = None


@router.get("/connectors", response_model=list[ConnectorOut], tags=["reference"])
def connectors(user: User = Depends(current_user), db: Session = Depends(get_db)):
    health = {h.connector: h for h in db.scalars(select(ConnectorHealth))}
    out = []
    for m in registry.manifests():
        h = health.get(m.key)
        out.append(
            ConnectorOut(
                **m.model_dump(), health=h.status if h else None, health_updated_at=h.updated_at if h else None
            )
        )
    return out


# --------------------------------------------------------- notifications
class NotificationOut(ORM):
    id: int
    kind: str
    title: str
    body: str | None
    data: dict
    read_at: datetime | None
    created_at: datetime


@router.get("/notifications", response_model=list[NotificationOut], tags=["notifications"])
def notifications(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = select(Notification).where(
        Notification.organisation_id == user.organisation_id,
        or_(Notification.user_id == user.id, Notification.user_id.is_(None)),
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit)).all()


@router.post("/notifications/read-all", tags=["notifications"])
def read_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    res = db.execute(
        update(Notification)
        .where(
            Notification.organisation_id == user.organisation_id,
            Notification.read_at.is_(None),
            or_(Notification.user_id == user.id, Notification.user_id.is_(None)),
        )
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
    return {"updated": res.rowcount}


class AlertRuleIn(BaseModel):
    rule_type: str = Field(
        pattern="^(price_drop_pct|price_rise_pct|price_below|back_in_stock|out_of_stock|rank_improved_pct)$"
    )
    threshold: Decimal | None = None
    listing_id: int | None = None
    project_id: uuid.UUID | None = None


class AlertRuleOut(ORM):
    id: int
    rule_type: str
    threshold: Decimal | None
    listing_id: int | None
    project_id: uuid.UUID | None
    is_enabled: bool
    created_at: datetime


@router.get("/alert-rules", response_model=list[AlertRuleOut], tags=["notifications"])
def list_rules(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(AlertRule).where(AlertRule.organisation_id == user.organisation_id).order_by(AlertRule.created_at.desc())
    ).all()


@router.post("/alert-rules", response_model=AlertRuleOut, status_code=201, tags=["notifications"])
def create_rule(body: AlertRuleIn, user: User = Depends(require(Permission.RUN_JOBS)), db: Session = Depends(get_db)):
    if (body.listing_id is None) == (body.project_id is None):
        raise AppError("Provide exactly one of listing_id or project_id")
    if body.listing_id is not None:
        listing = db.get(Listing, body.listing_id)
        if listing is None or listing.organisation_id != user.organisation_id:
            raise NotFound("Product not found")
    if body.project_id is not None:
        project = db.get(ResearchProject, body.project_id)
        if project is None or project.organisation_id != user.organisation_id:
            raise NotFound("Project not found")
    if body.rule_type not in ("back_in_stock", "out_of_stock") and body.threshold is None:
        raise AppError("This rule type needs a threshold")
    rule = AlertRule(organisation_id=user.organisation_id, user_id=user.id, **body.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/alert-rules/{rule_id}", status_code=204, tags=["notifications"])
def delete_rule(rule_id: int, user: User = Depends(require(Permission.RUN_JOBS)), db: Session = Depends(get_db)):
    rule = db.get(AlertRule, rule_id)
    if rule is None or rule.organisation_id != user.organisation_id:
        raise NotFound("Alert rule not found")
    db.delete(rule)
    db.commit()


# ------------------------------------------------------------------ audit
class AuditOut(ORM):
    id: int
    user_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    ip_address: str | None = None
    details: dict
    created_at: datetime


@router.get("/audit-logs", response_model=list[AuditOut], tags=["audit"])
def audit_logs(
    user: User = Depends(require(Permission.VIEW_AUDIT)),
    db: Session = Depends(get_db),
    action: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    stmt = select(AuditLog).where(AuditLog.organisation_id == user.organisation_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return [
        AuditOut.model_validate(
            {
                **{c: getattr(r, c) for c in AuditOut.model_fields if c != "ip_address"},
                "ip_address": str(r.ip_address) if r.ip_address else None,
            }
        )
        for r in rows
    ]


# ----------------------------------------------------------------- health
health_router = APIRouter(tags=["health"])


@health_router.get("/health")
def health():
    return {"status": "ok"}


@health_router.get("/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}
