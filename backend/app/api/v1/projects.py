from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, require
from app.core.db import get_db
from app.core.errors import AppError, Conflict, NotFound
from app.core.security import Permission
from app.models import CompetitorShop, Listing, Marketplace, ProjectProduct, ResearchProject, SavedSearch, User
from app.schemas.common import ORM

router = APIRouter(tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class ProjectOut(ORM):
    id: uuid.UUID
    name: str
    description: str | None
    archived_at: datetime | None
    created_at: datetime
    product_count: int = 0


def _project(db: Session, user: User, project_id: uuid.UUID) -> ResearchProject:
    p = db.get(ResearchProject, project_id)
    if p is None or p.organisation_id != user.organisation_id:
        raise NotFound("Project not found")
    return p


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db), include_archived: bool = False):
    counts = select(ProjectProduct.project_id, func.count().label("n")).group_by(ProjectProduct.project_id).subquery()
    stmt = (
        select(ResearchProject, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.project_id == ResearchProject.id)
        .where(ResearchProject.organisation_id == user.organisation_id)
    )
    if not include_archived:
        stmt = stmt.where(ResearchProject.archived_at.is_(None))
    out = []
    for p, n in db.execute(stmt.order_by(ResearchProject.created_at.desc())):
        item = ProjectOut.model_validate(p)
        item.product_count = n
        out.append(item)
    return out


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectIn, user: User = Depends(require(Permission.MANAGE_PROJECTS)), db: Session = Depends(get_db)
):
    p = ResearchProject(
        organisation_id=user.organisation_id, name=body.name, description=body.description, created_by=user.id
    )
    db.add(p)
    try:
        db.commit()
    except IntegrityError as exc:
        raise Conflict("A project with this name already exists") from exc
    db.refresh(p)
    return p


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID,
    body: ProjectIn,
    user: User = Depends(require(Permission.MANAGE_PROJECTS)),
    db: Session = Depends(get_db),
):
    p = _project(db, user, project_id)
    p.name, p.description = body.name, body.description
    db.commit()
    db.refresh(p)
    return p


@router.post("/projects/{project_id}/archive", response_model=ProjectOut)
def archive_project(
    project_id: uuid.UUID, user: User = Depends(require(Permission.MANAGE_PROJECTS)), db: Session = Depends(get_db)
):
    p = _project(db, user, project_id)
    p.archived_at = None if p.archived_at else datetime.now(UTC)
    db.commit()
    db.refresh(p)
    return p


class AddProducts(BaseModel):
    listing_ids: list[int] = Field(min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/projects/{project_id}/products")
def add_products(
    project_id: uuid.UUID,
    body: AddProducts,
    user: User = Depends(require(Permission.MANAGE_PROJECTS)),
    db: Session = Depends(get_db),
):
    _project(db, user, project_id)
    valid = list(
        db.scalars(
            select(Listing.id).where(Listing.organisation_id == user.organisation_id, Listing.id.in_(body.listing_ids))
        )
    )
    if valid:
        stmt = pg_insert(ProjectProduct.__table__).values(
            [
                {
                    "project_id": project_id,
                    "listing_id": lid,
                    "tags": body.tags,
                    "notes": body.notes,
                    "added_by": user.id,
                }
                for lid in valid
            ]
        )
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["project_id", "listing_id"],
                set_={
                    "tags": text("ARRAY(SELECT DISTINCT unnest(project_products.tags || EXCLUDED.tags))"),
                    "notes": func.coalesce(stmt.excluded.notes, ProjectProduct.__table__.c.notes),
                },
            )
        )
    db.commit()
    return {"added": len(valid), "ignored": len(set(body.listing_ids)) - len(valid)}


class RemoveProducts(BaseModel):
    listing_ids: list[int] = Field(min_length=1, max_length=5000)


@router.post("/projects/{project_id}/products/remove")
def remove_products(
    project_id: uuid.UUID,
    body: RemoveProducts,
    user: User = Depends(require(Permission.MANAGE_PROJECTS)),
    db: Session = Depends(get_db),
):
    _project(db, user, project_id)
    res = db.execute(
        delete(ProjectProduct).where(
            ProjectProduct.project_id == project_id, ProjectProduct.listing_id.in_(body.listing_ids)
        )
    )
    db.commit()
    return {"removed": res.rowcount}


class ShopIn(BaseModel):
    marketplace_code: str
    shop_url: str = Field(max_length=2048)
    display_name: str | None = None


class ShopOut(ORM):
    id: int
    marketplace_id: int
    shop_url: str
    external_seller_id: str | None
    display_name: str | None
    created_at: datetime


@router.get("/projects/{project_id}/shops", response_model=list[ShopOut])
def list_shops(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _project(db, user, project_id)
    return db.scalars(select(CompetitorShop).where(CompetitorShop.project_id == project_id)).all()


@router.post("/projects/{project_id}/shops", response_model=ShopOut, status_code=201)
def add_shop(
    project_id: uuid.UUID,
    body: ShopIn,
    user: User = Depends(require(Permission.MANAGE_PROJECTS)),
    db: Session = Depends(get_db),
):
    from app.core.ssrf import normalise_url

    _project(db, user, project_id)
    mp = db.scalar(select(Marketplace).where(Marketplace.code == body.marketplace_code))
    if mp is None:
        raise AppError("Unknown marketplace")
    shop = CompetitorShop(
        project_id=project_id,
        marketplace_id=mp.id,
        shop_url=normalise_url(body.shop_url),
        display_name=body.display_name,
    )
    db.add(shop)
    try:
        db.commit()
    except IntegrityError as exc:
        raise Conflict("This shop is already tracked in the project") from exc
    db.refresh(shop)
    return shop


class SavedSearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(job_input|product_filter)$")
    parameters: dict
    project_id: uuid.UUID | None = None
    schedule_cron: str | None = Field(default=None, max_length=100)


class SavedSearchOut(ORM):
    id: int
    name: str
    kind: str
    parameters: dict
    project_id: uuid.UUID | None
    schedule_cron: str | None
    created_at: datetime


@router.get("/saved-searches", response_model=list[SavedSearchOut])
def list_saved(user: User = Depends(current_user), db: Session = Depends(get_db), kind: str | None = None):
    stmt = select(SavedSearch).where(SavedSearch.organisation_id == user.organisation_id)
    if kind:
        stmt = stmt.where(SavedSearch.kind == kind)
    return db.scalars(stmt.order_by(SavedSearch.created_at.desc())).all()


@router.post("/saved-searches", response_model=SavedSearchOut, status_code=201)
def save_search(
    body: SavedSearchIn, user: User = Depends(require(Permission.MANAGE_PROJECTS)), db: Session = Depends(get_db)
):
    if body.project_id:
        _project(db, user, body.project_id)
    s = SavedSearch(organisation_id=user.organisation_id, user_id=user.id, **body.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/saved-searches/{search_id}", status_code=204)
def delete_saved(
    search_id: int, user: User = Depends(require(Permission.MANAGE_PROJECTS)), db: Session = Depends(get_db)
):
    s = db.get(SavedSearch, search_id)
    if s is None or s.organisation_id != user.organisation_id:
        raise NotFound("Saved search not found")
    db.delete(s)
    db.commit()
