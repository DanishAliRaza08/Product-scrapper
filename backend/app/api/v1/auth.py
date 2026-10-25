from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, current_user, login_limiter, request_context, require
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized
from app.core.security import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.models import Organisation, RefreshToken, User
from app.schemas.common import ORM
from app.services import audit

router = APIRouter(tags=["auth"])
REFRESH_COOKIE = "mpi_refresh"
# Dummy hash so unknown emails take as long as wrong passwords (no user enumeration by timing).
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


class RegisterIn(BaseModel):
    organisation_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    full_name: str | None = Field(default=None, max_length=120)
    base_currency: str = Field(default="GBP", pattern="^[A-Z]{3}$")


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    client_type: str = Field(default="web", pattern="^(web|desktop)$")


class RefreshIn(BaseModel):
    refresh_token: str | None = None


class UserOut(ORM):
    id: uuid.UUID
    organisation_id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    is_active: bool
    export_row_limit: int | None = None
    created_at: datetime


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    refresh_token: str | None = None  # returned to desktop clients (stored in the OS keychain)
    user: UserOut
    organisation_name: str
    permissions: list[str]


def _issue(
    db: Session,
    user: User,
    response: Response,
    client_type: str,
    family: uuid.UUID | None = None,
    previous: RefreshToken | None = None,
) -> SessionOut:
    settings = get_settings()
    access, ttl = create_access_token(user_id=user.id, organisation_id=user.organisation_id, role=user.role)
    plain, digest = new_refresh_token()
    token = RefreshToken(
        user_id=user.id,
        token_hash=digest,
        family_id=family or uuid.uuid4(),
        client_type=client_type,
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
    )
    db.add(token)
    db.flush()
    if previous is not None:
        previous.revoked_at = datetime.now(UTC)
        previous.replaced_by = token.id
    if client_type == "web":
        response.set_cookie(
            REFRESH_COOKIE,
            plain,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            max_age=settings.refresh_token_days * 86400,
            path=f"{settings.api_prefix}/auth",
        )
    return SessionOut(
        access_token=access,
        expires_in=ttl,
        refresh_token=plain if client_type == "desktop" else None,
        user=UserOut.model_validate(user),
        organisation_name=user.organisation.name,
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(user.role)]),
    )


@router.post("/auth/register", response_model=SessionOut, status_code=201)
def register(
    body: RegisterIn, response: Response, db: Session = Depends(get_db), ctx: RequestContext = Depends(request_context)
) -> SessionOut:
    if not get_settings().allow_self_signup:
        raise Forbidden("Self sign-up is disabled; ask an administrator for an invitation")
    login_limiter.hit(f"register:{ctx.ip}")
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())):
        raise Conflict("An account with this email already exists")
    org = Organisation(name=body.organisation_name, base_currency=body.base_currency)
    db.add(org)
    db.flush()
    user = User(
        organisation_id=org.id,
        email=body.email.lower(),
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=Role.ADMIN.value,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    audit.record(
        db,
        action="organisation.created",
        organisation_id=org.id,
        user_id=user.id,
        ip_address=ctx.ip,
        user_agent=ctx.user_agent,
    )
    session = _issue(db, user, response, "web")
    db.commit()
    return session


@router.post("/auth/login", response_model=SessionOut)
def login(
    body: LoginIn, response: Response, db: Session = Depends(get_db), ctx: RequestContext = Depends(request_context)
) -> SessionOut:
    login_limiter.hit(f"login:{ctx.ip}:{body.email.lower()}")
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None:
        verify_password(_DUMMY_HASH, body.password)
        raise Unauthorized("Invalid email or password")
    if not verify_password(user.password_hash, body.password) or not user.is_active:
        audit.record(
            db,
            action="auth.login_failed",
            organisation_id=user.organisation_id,
            user_id=user.id,
            ip_address=ctx.ip,
            user_agent=ctx.user_agent,
        )
        db.commit()
        raise Unauthorized("Invalid email or password")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)
    audit.record(
        db,
        action="auth.login",
        organisation_id=user.organisation_id,
        user_id=user.id,
        ip_address=ctx.ip,
        user_agent=ctx.user_agent,
        client_type=body.client_type,
    )
    session = _issue(db, user, response, body.client_type)
    db.commit()
    return session


@router.post("/auth/refresh", response_model=SessionOut)
def refresh(
    response: Response,
    body: RefreshIn | None = None,
    db: Session = Depends(get_db),
    cookie_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> SessionOut:
    plain = (body.refresh_token if body else None) or cookie_token
    if not plain:
        raise Unauthorized("No refresh token")
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(plain)).with_for_update())
    if token is None:
        raise Unauthorized("Invalid refresh token")
    if token.revoked_at is not None:
        # Reuse of a rotated token signals theft: revoke the whole family.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        audit.record(db, action="auth.refresh_reuse_detected", organisation_id=None, user_id=token.user_id)
        db.commit()
        raise Unauthorized("Refresh token reuse detected; please sign in again")
    if token.expires_at < datetime.now(UTC):
        raise Unauthorized("Refresh token expired")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("User is inactive")
    session = _issue(db, user, response, token.client_type, family=token.family_id, previous=token)
    db.commit()
    return session


@router.post("/auth/logout", status_code=204)
def logout(
    response: Response,
    body: RefreshIn | None = None,
    db: Session = Depends(get_db),
    cookie_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> Response:
    plain = (body.refresh_token if body else None) or cookie_token
    if plain:
        token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(plain)))
        if token is not None:
            db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
            db.commit()
    response.delete_cookie(REFRESH_COOKIE, path=f"{get_settings().api_prefix}/auth")
    response.status_code = 204
    return response


@router.get("/auth/me", response_model=SessionOut)
def me(user: User = Depends(current_user)) -> SessionOut:
    access, ttl = create_access_token(user_id=user.id, organisation_id=user.organisation_id, role=user.role)
    return SessionOut(
        access_token=access,
        expires_in=ttl,
        user=UserOut.model_validate(user),
        organisation_name=user.organisation.name,
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(user.role)]),
    )


# ------------------------------------------------------------------ users
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    full_name: str | None = None
    role: Role = Role.RESEARCHER
    export_row_limit: int | None = Field(default=None, ge=1)


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    full_name: str | None = None
    export_row_limit: int | None = Field(default=None, ge=1)


@router.get("/users", response_model=list[UserOut], tags=["users"])
def list_users(admin: User = Depends(require(Permission.MANAGE_USERS)), db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.organisation_id == admin.organisation_id).order_by(User.created_at)).all()


@router.post("/users", response_model=UserOut, status_code=201, tags=["users"])
def create_user(
    body: UserCreate,
    admin: User = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(request_context),
):
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())):
        raise Conflict("A user with this email already exists")
    user = User(
        organisation_id=admin.organisation_id,
        email=body.email.lower(),
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=body.role.value,
        export_row_limit=body.export_row_limit,
    )
    db.add(user)
    db.flush()
    audit.record(
        db,
        action="user.created",
        organisation_id=admin.organisation_id,
        user_id=admin.id,
        resource_type="user",
        resource_id=str(user.id),
        role=body.role.value,
        ip_address=ctx.ip,
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut, tags=["users"])
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    admin: User = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.organisation_id != admin.organisation_id:
        raise NotFound("User not found")
    changes = body.model_dump(exclude_unset=True)
    if user.id == admin.id and (changes.get("role", Role.ADMIN) != Role.ADMIN or changes.get("is_active") is False):
        raise Conflict("You cannot demote or deactivate yourself")
    for key, value in changes.items():
        setattr(user, key, value.value if isinstance(value, Role) else value)
    if changes.get("is_active") is False or "role" in changes:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
    audit.record(
        db,
        action="user.updated",
        organisation_id=admin.organisation_id,
        user_id=admin.id,
        resource_type="user",
        resource_id=str(user.id),
        **{k: str(v) for k, v in changes.items()},
    )
    db.commit()
    db.refresh(user)
    return user
