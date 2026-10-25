"""Own seller account connections (OAuth) and synchronisation jobs."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, current_user, request_context, require
from app.connectors.base import ConnectorError
from app.connectors.registry import get_connector
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.core.secrets import get_secret_store
from app.core.security import Permission, has_permission
from app.models import Job, Marketplace, MarketplaceAccount, OAuthState, SyncCheckpoint, User
from app.schemas.common import ORM
from app.services import audit
from app.services import jobs as job_service
from app.services.events import emit
from app.services.jobs import run_async

router = APIRouter(tags=["connections"])


class ConnectionIn(BaseModel):
    marketplace_code: str
    account_name: str


class CheckpointOut(ORM):
    stream: str
    last_successful_sync_at: datetime | None
    last_attempt_at: datetime | None
    retry_count: int
    last_error: str | None


class ConnectionOut(ORM):
    id: uuid.UUID
    marketplace_code: str = ""
    account_name: str
    external_account_id: str | None
    connection_status: str
    granted_scopes: list[str]
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    checkpoints: list[CheckpointOut] = []
    authorization_url: str | None = None


def _out(db: Session, a: MarketplaceAccount, authorization_url: str | None = None) -> ConnectionOut:
    out = ConnectionOut.model_validate(a)
    out.marketplace_code = a.marketplace.code
    out.checkpoints = [
        CheckpointOut.model_validate(c)
        for c in db.scalars(select(SyncCheckpoint).where(SyncCheckpoint.account_id == a.id))
    ]
    out.authorization_url = authorization_url
    return out


def _redirect_uri(platform: str) -> str:
    s = get_settings()
    return f"{s.public_api_url}{s.api_prefix}/connections/callback/{platform}"


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not has_permission(user.role, Permission.VIEW_OWN_ACCOUNT_DATA):
        return []
    return [
        _out(db, a)
        for a in db.scalars(
            select(MarketplaceAccount)
            .where(MarketplaceAccount.organisation_id == user.organisation_id)
            .order_by(MarketplaceAccount.created_at)
        )
    ]


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def start_connection(
    body: ConnectionIn,
    user: User = Depends(require(Permission.MANAGE_CONNECTIONS)),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(request_context),
):
    mp = db.scalar(select(Marketplace).where(Marketplace.code == body.marketplace_code))
    if mp is None:
        raise AppError("Unknown marketplace")
    connector = get_connector(mp.platform)
    account = MarketplaceAccount(
        organisation_id=user.organisation_id, marketplace_id=mp.id, account_name=body.account_name, created_by=user.id
    )
    db.add(account)
    db.flush()
    state = secrets.token_urlsafe(32)
    try:
        start = connector.start_oauth(mp.code, state, _redirect_uri(mp.platform))
    except ConnectorError as exc:
        raise AppError(exc.message, code=exc.code) from exc
    db.add(
        OAuthState(
            state=state, account_id=account.id, user_id=user.id, expires_at=datetime.now(UTC) + timedelta(minutes=15)
        )
    )
    audit.record(
        db,
        action="connection.started",
        organisation_id=user.organisation_id,
        user_id=user.id,
        resource_type="marketplace_account",
        resource_id=str(account.id),
        marketplace=mp.code,
        ip_address=ctx.ip,
    )
    db.commit()
    db.refresh(account)
    return _out(db, account, start.authorization_url)


@router.get("/connections/callback/{platform}", include_in_schema=False)
def oauth_callback(platform: str, request: Request, db: Session = Depends(get_db)):
    """Browser redirect target from the marketplace consent screen. Authenticated by the
    single-use, expiring ``state`` value bound to the initiating user."""
    params = dict(request.query_params)
    web = get_settings().public_web_url
    state = db.get(OAuthState, params.get("state", ""))
    if state is None or state.expires_at < datetime.now(UTC):
        return RedirectResponse(f"{web}/connections?error=invalid_state", status_code=302)
    account = db.get(MarketplaceAccount, state.account_id)
    db.delete(state)  # single use
    if account is None or account.marketplace.platform != platform:
        db.commit()
        return RedirectResponse(f"{web}/connections?error=invalid_state", status_code=302)
    try:
        result = run_async(
            get_connector(platform).complete_oauth(account.marketplace.code, params, _redirect_uri(platform))
        )
    except ConnectorError as exc:
        account.connection_status = "error"
        account.last_error = exc.message
        db.commit()
        return RedirectResponse(f"{web}/connections?error={exc.code}", status_code=302)
    store = get_secret_store()
    if account.credential_reference:
        store.update(account.credential_reference, result.credentials.model_dump(mode="json"))
    else:
        account.credential_reference = store.put(result.credentials.model_dump(mode="json"))
    account.external_account_id = result.external_account_id
    account.granted_scopes = result.granted_scopes
    account.connection_status = "connected"
    account.last_error = None
    audit.record(
        db,
        action="connection.authorised",
        organisation_id=account.organisation_id,
        user_id=state.user_id,
        resource_type="marketplace_account",
        resource_id=str(account.id),
    )
    db.commit()
    return RedirectResponse(f"{web}/connections?connected={account.id}", status_code=302)


class SyncIn(BaseModel):
    streams: list[str] = ["catalogue", "orders"]


@router.post("/connections/{account_id}/sync", status_code=202)
def sync_connection(
    account_id: uuid.UUID,
    body: SyncIn | None = None,
    user: User = Depends(require(Permission.MANAGE_CONNECTIONS)),
    db: Session = Depends(get_db),
):
    account = db.get(MarketplaceAccount, account_id)
    if account is None or account.organisation_id != user.organisation_id:
        raise NotFound("Connection not found")
    if account.connection_status != "connected":
        raise AppError("Account is not connected; complete authorisation first", code="not_connected")
    job = Job(
        organisation_id=user.organisation_id,
        user_id=user.id,
        account_id=account.id,
        marketplace_id=account.marketplace_id,
        job_type="account_sync",
        input_type="account",
        configuration={"streams": (body or SyncIn()).streams},
    )
    db.add(job)
    db.flush()
    emit(db, job.id, "created", f"Sync requested for {account.account_name}")
    db.commit()
    job_service.enqueue(job.id)
    return {"job_id": str(job.id)}


@router.delete("/connections/{account_id}", status_code=204)
def disconnect(
    account_id: uuid.UUID,
    user: User = Depends(require(Permission.MANAGE_CONNECTIONS)),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(request_context),
):
    account = db.get(MarketplaceAccount, account_id)
    if account is None or account.organisation_id != user.organisation_id:
        raise NotFound("Connection not found")
    if account.credential_reference:
        get_secret_store().delete(account.credential_reference)
    account.credential_reference = None
    account.connection_status = "revoked"
    audit.record(
        db,
        action="connection.revoked",
        organisation_id=user.organisation_id,
        user_id=user.id,
        resource_type="marketplace_account",
        resource_id=str(account.id),
        ip_address=ctx.ip,
    )
    db.commit()
