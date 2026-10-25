from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, current_user, request_context, require
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, Forbidden, NotFound
from app.core.security import Permission, has_permission
from app.core.storage import get_storage
from app.models import Export, User
from app.schemas.common import ORM
from app.services import audit
from app.services import exports as export_service
from app.services.products import ProductFilters

router = APIRouter(tags=["exports"])


class ExportIn(BaseModel):
    export_format: str = Field(default="xlsx", pattern="^(xlsx|csv)$")
    filters: ProductFilters = Field(default_factory=ProductFilters)
    sheets: list[str] = Field(default_factory=lambda: list(export_service.ALL_SHEETS))
    job_id: uuid.UUID | None = None


class ExportOut(ORM):
    id: uuid.UUID
    export_format: str
    export_status: str
    filters: dict
    sheets: list[str]
    includes_own_account_data: bool
    file_size_bytes: int | None
    row_count: int | None
    progress: Decimal
    error_summary: str | None
    manifest: dict | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None


@router.post("/exports", response_model=ExportOut, status_code=202)
def create_export(
    body: ExportIn,
    user: User = Depends(require(Permission.EXPORT)),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(request_context),
):
    unknown = set(body.sheets) - set(export_service.ALL_SHEETS)
    if unknown:
        raise AppError(f"Unknown sheets: {sorted(unknown)}")
    filters = body.filters
    if not has_permission(user.role, Permission.EXPORT_OWN_ACCOUNT_DATA):
        filters.include_own_account = False  # own-account data needs the dedicated permission
    export = Export(
        organisation_id=user.organisation_id,
        user_id=user.id,
        job_id=body.job_id,
        export_format=body.export_format,
        filters=filters.model_dump(mode="json", exclude_none=True),
        sheets=body.sheets,
    )
    db.add(export)
    db.flush()
    audit.record(
        db,
        action="export.requested",
        organisation_id=user.organisation_id,
        user_id=user.id,
        resource_type="export",
        resource_id=str(export.id),
        ip_address=ctx.ip,
        format=body.export_format,
    )
    db.commit()
    if get_settings().celery_eager:
        export_service.generate(export.id)
    else:
        from app.workers.tasks import export_task

        export_task.delay(str(export.id))
    db.refresh(export)
    return export


@router.get("/exports", response_model=list[ExportOut])
def list_exports(
    user: User = Depends(current_user), db: Session = Depends(get_db), limit: int = Query(default=50, ge=1, le=200)
):
    stmt = select(Export).where(Export.organisation_id == user.organisation_id)
    if not has_permission(user.role, Permission.MANAGE_USERS):
        stmt = stmt.where(Export.user_id == user.id)
    return db.scalars(stmt.order_by(Export.created_at.desc()).limit(limit)).all()


def _export(db: Session, user: User, export_id: uuid.UUID) -> Export:
    export = db.get(Export, export_id)
    if export is None or export.organisation_id != user.organisation_id:
        raise NotFound("Export not found")
    if export.user_id != user.id and not has_permission(user.role, Permission.MANAGE_USERS):
        raise Forbidden("You can only access your own exports")
    return export


@router.get("/exports/{export_id}", response_model=ExportOut)
def get_export(export_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _export(db, user, export_id)


@router.post("/exports/{export_id}/link")
def export_link(
    export_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(request_context),
):
    """Issue a short-lived, user-bound download link."""
    export = _export(db, user, export_id)
    if export.export_status != "completed":
        raise AppError(f"Export is {export.export_status}", code="export_not_ready")
    if export.includes_own_account_data and not has_permission(user.role, Permission.EXPORT_OWN_ACCOUNT_DATA):
        raise Forbidden("This export contains own-account data")
    expires = datetime.now(UTC) + timedelta(minutes=get_settings().download_link_minutes)
    token = export_service.download_token(export.id, user.id, expires)
    audit.record(
        db,
        action="export.link_issued",
        organisation_id=user.organisation_id,
        user_id=user.id,
        resource_type="export",
        resource_id=str(export.id),
        ip_address=ctx.ip,
        own_account=export.includes_own_account_data,
    )
    db.commit()
    s = get_settings()
    return {
        "url": f"{s.public_api_url}{s.api_prefix}/exports/{export.id}/download?u={user.id}&token={token}",
        "expires_at": expires.isoformat(),
    }


@router.get("/exports/{export_id}/download")
def download(export_id: uuid.UUID, u: uuid.UUID, token: str, db: Session = Depends(get_db)):
    if not export_service.verify_download_token(export_id, u, token):
        raise Forbidden("Download link is invalid or expired")
    export = db.get(Export, export_id)
    if export is None or export.export_status != "completed" or not export.storage_key:
        raise NotFound("Export not available")
    user = db.get(User, u)
    if user is None or not user.is_active or user.organisation_id != export.organisation_id:
        raise Forbidden("Download link is invalid or expired")
    fh = get_storage().open(export.storage_key)
    filename = f"product-export-{export.created_at:%Y%m%d-%H%M}.{export.export_format}"
    return StreamingResponse(
        iter(lambda: fh.read(64 * 1024), b""),
        media_type=export_service.CONTENT_TYPES[export.export_format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )
