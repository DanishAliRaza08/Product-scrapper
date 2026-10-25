"""Evaluate alert rules against listing changes and raise notifications."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.models import AlertRule, Notification, ProjectProduct
from app.services.ingest import ListingChange


def _pct(old: Decimal | int | None, new: Decimal | int | None) -> Decimal | None:
    if old in (None, 0) or new is None:
        return None
    return (Decimal(new) - Decimal(old)) / Decimal(old) * 100


def triggered(rule_type: str, threshold: Decimal | None, c: ListingChange) -> str | None:
    if c.is_new:
        return None
    if rule_type == "price_drop_pct":
        pct = _pct(c.old_price, c.new_price)
        if pct is not None and -pct >= (threshold or 0) and pct < 0:
            return f"Price dropped {abs(pct):.1f}% ({c.old_price} → {c.new_price} {c.currency or ''})".strip()
    elif rule_type == "price_rise_pct":
        pct = _pct(c.old_price, c.new_price)
        if pct is not None and pct >= (threshold or 0) and pct > 0:
            return f"Price rose {pct:.1f}% ({c.old_price} → {c.new_price} {c.currency or ''})".strip()
    elif rule_type == "price_below":
        if c.new_price is not None and threshold is not None and c.new_price < threshold <= (c.old_price or threshold):
            return f"Price fell below {threshold} ({c.new_price} {c.currency or ''})".strip()
    elif rule_type == "back_in_stock":
        if c.old_stock == "out_of_stock" and c.new_stock in ("in_stock", "low_stock"):
            return "Back in stock"
    elif rule_type == "out_of_stock":
        if c.old_stock in ("in_stock", "low_stock") and c.new_stock == "out_of_stock":
            return "Went out of stock"
    elif rule_type == "rank_improved_pct":
        # Lower rank number is better.
        pct = _pct(c.old_rank, c.new_rank)
        if pct is not None and pct < 0 and -pct >= (threshold or 0):
            return f"Sales rank improved {abs(pct):.1f}% ({c.old_rank} → {c.new_rank})"
    return None


def evaluate(db: Session, organisation_id: uuid.UUID, changes: list[ListingChange]) -> int:
    changed = [c for c in changes if not c.is_new]
    if not changed:
        return 0
    listing_ids = [c.listing_id for c in changed]
    project_ids_by_listing: dict[int, set[uuid.UUID]] = {}
    for pid, lid in db.execute(
        select(ProjectProduct.project_id, ProjectProduct.listing_id).where(ProjectProduct.listing_id.in_(listing_ids))
    ):
        project_ids_by_listing.setdefault(lid, set()).add(pid)
    all_projects = {p for s in project_ids_by_listing.values() for p in s}
    rules = db.scalars(
        select(AlertRule).where(
            AlertRule.organisation_id == organisation_id,
            AlertRule.is_enabled.is_(True),
            or_(
                AlertRule.listing_id.in_(listing_ids),
                AlertRule.project_id.in_(all_projects) if all_projects else false(),
            ),
        )
    ).all()
    created = 0
    for c in changed:
        for rule in rules:
            applies = rule.listing_id == c.listing_id or (
                rule.project_id is not None and rule.project_id in project_ids_by_listing.get(c.listing_id, set())
            )
            if not applies:
                continue
            message = triggered(rule.rule_type, rule.threshold, c)
            if message:
                db.add(
                    Notification(
                        organisation_id=organisation_id,
                        user_id=rule.user_id,
                        kind="alert",
                        title=message,
                        body=f"Listing {c.external_id}",
                        data={"listing_id": c.listing_id, "rule_id": rule.id, "rule_type": rule.rule_type},
                    )
                )
                created += 1
    return created
