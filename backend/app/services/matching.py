"""Cross-marketplace product matching.

Exact GTIN (normalised to GTIN-14 with check digit validation) is tried first; then
brand + model number; then a weighted similarity of title tokens, attributes,
variations and price sanity. Results are stored as *candidates* - nothing is merged
until a user confirms it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.models import CanonicalProduct, Listing, ProductMatch
from app.services.fx import convert
from app.services.identifiers import normalise_model, normalise_title, title_tokens

MIN_CANDIDATE_SCORE = 0.55


@dataclass
class MatchScore:
    method: str
    score: float
    evidence: dict


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_pair(a: Listing, b: Listing, price_ratio: float | None = None) -> MatchScore:
    if a.gtin and b.gtin and a.gtin == b.gtin:
        return MatchScore("gtin_exact", 0.99, {"gtin": a.gtin})
    if a.gtin and b.gtin and a.gtin != b.gtin:
        # Conflicting valid identifiers are strong evidence of different products.
        return MatchScore("gtin_conflict", 0.0, {"gtin_a": a.gtin, "gtin_b": b.gtin})
    brand_a, brand_b = normalise_title(a.brand), normalise_title(b.brand)
    model_a, model_b = normalise_model(a.mpn), normalise_model(b.mpn)
    if brand_a and brand_a == brand_b and model_a and model_a == model_b:
        return MatchScore("brand_model", 0.92, {"brand": a.brand, "model": a.mpn})

    tokens_a, tokens_b = title_tokens(a.title), title_tokens(b.title)
    title_sim = jaccard(tokens_a, tokens_b)
    brand_sim = 1.0 if brand_a and brand_a == brand_b else (0.0 if brand_a and brand_b else 0.5)
    attrs_a = {k: str(v).lower() for k, v in (a.attributes or {}).items() if v not in (None, "", True, False)}
    attrs_b = {k: str(v).lower() for k, v in (b.attributes or {}).items() if v not in (None, "", True, False)}
    shared = set(attrs_a) & set(attrs_b)
    attr_sim = (sum(attrs_a[k] == attrs_b[k] for k in shared) / len(shared)) if shared else 0.5
    price_sim = 0.5
    if price_ratio is not None:
        # Same product rarely differs by more than 3x across regions after FX conversion.
        price_sim = 1.0 if 0.67 <= price_ratio <= 1.5 else 0.5 if 0.33 <= price_ratio <= 3 else 0.0
    score = 0.55 * title_sim + 0.2 * brand_sim + 0.15 * attr_sim + 0.10 * price_sim
    return MatchScore(
        "fuzzy",
        round(score, 4),
        {
            "title_similarity": round(title_sim, 3),
            "brand_similarity": brand_sim,
            "attribute_similarity": round(attr_sim, 3),
            "price_ratio": round(price_ratio, 3) if price_ratio else None,
            "shared_tokens": sorted(tokens_a & tokens_b)[:15],
        },
    )


def _price_ratio(db: Session, a: Listing, b: Listing) -> float | None:
    if not (a.current_price and b.current_price and a.currency_code and b.currency_code):
        return None
    converted = convert(db, b.current_price, b.currency_code, a.currency_code)
    if converted is None or converted == 0:
        return None
    return float(a.current_price / converted)


def _store(db: Session, organisation_id: uuid.UUID, a: Listing, b: Listing, m: MatchScore) -> None:
    src, tgt = sorted((a.id, b.id))
    stmt = pg_insert(ProductMatch.__table__).values(
        organisation_id=organisation_id,
        source_listing_id=src,
        target_listing_id=tgt,
        match_method=m.method,
        score=Decimal(str(m.score)),
        evidence=m.evidence,
    )
    # Never overwrite a human decision.
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["source_listing_id", "target_listing_id"],
            set_={
                "match_method": stmt.excluded.match_method,
                "score": stmt.excluded.score,
                "evidence": stmt.excluded.evidence,
            },
            where=ProductMatch.__table__.c.status == "candidate",
        )
    )


def find_candidates(db: Session, organisation_id: uuid.UUID, listing_id: int, limit: int = 20) -> int:
    listing = db.get(Listing, listing_id)
    if listing is None or listing.organisation_id != organisation_id:
        raise NotFound("Listing not found")
    conditions = [
        Listing.organisation_id == organisation_id,
        Listing.id != listing.id,
        Listing.marketplace_id != listing.marketplace_id,
    ]
    identity = []
    if listing.gtin:
        identity.append(Listing.gtin == listing.gtin)
    if listing.brand and listing.mpn:
        identity.append(and_(func.lower(Listing.brand) == listing.brand.lower(), Listing.mpn == listing.mpn))
    candidates: list[Listing] = []
    if identity:
        candidates = list(db.scalars(select(Listing).where(*conditions, or_(*identity)).limit(limit)))
    if len(candidates) < limit:
        similarity = func.similarity(Listing.title, listing.title)
        candidates += list(
            db.scalars(
                select(Listing)
                .where(*conditions, Listing.title.op("%")(listing.title), Listing.id.notin_([c.id for c in candidates]))
                .order_by(similarity.desc())
                .limit(limit - len(candidates))
            )
        )
    stored = 0
    for c in candidates:
        m = score_pair(listing, c, _price_ratio(db, listing, c))
        if m.score >= MIN_CANDIDATE_SCORE:
            _store(db, organisation_id, listing, c, m)
            stored += 1
    return stored


def auto_match_by_gtin(db: Session, organisation_id: uuid.UUID, listing_ids: list[int]) -> int:
    """Cheap pass run after ingestion: pair listings sharing a GTIN across marketplaces."""
    if not listing_ids:
        return 0
    a = Listing.__table__.alias("a")
    b = Listing.__table__.alias("b")
    pairs = db.execute(
        select(a.c.id, b.c.id, a.c.gtin)
        .join(
            b,
            and_(
                a.c.organisation_id == b.c.organisation_id,
                a.c.gtin == b.c.gtin,
                a.c.marketplace_id != b.c.marketplace_id,
                a.c.id != b.c.id,
            ),
        )
        .where(a.c.organisation_id == organisation_id, a.c.id.in_(listing_ids), a.c.gtin.is_not(None))
        .limit(5000)
    ).all()
    seen = set()
    for x, y, gtin in pairs:
        key = tuple(sorted((x, y)))
        if key in seen:
            continue
        seen.add(key)
        stmt = pg_insert(ProductMatch.__table__).values(
            organisation_id=organisation_id,
            source_listing_id=key[0],
            target_listing_id=key[1],
            match_method="gtin_exact",
            score=Decimal("0.99"),
            evidence={"gtin": gtin},
        )
        db.execute(stmt.on_conflict_do_nothing(index_elements=["source_listing_id", "target_listing_id"]))
    return len(seen)


def review(db: Session, organisation_id: uuid.UUID, match_id: int, decision: str, user_id: uuid.UUID) -> ProductMatch:
    match = db.get(ProductMatch, match_id)
    if match is None or match.organisation_id != organisation_id:
        raise NotFound("Match not found")
    if decision not in ("confirmed", "rejected"):
        raise AppError("Decision must be 'confirmed' or 'rejected'")
    match.status = decision
    match.reviewed_by = user_id
    match.reviewed_at = datetime.now(UTC)
    if decision == "confirmed":
        a, b = db.get(Listing, match.source_listing_id), db.get(Listing, match.target_listing_id)
        assert a is not None and b is not None
        canonical_id = a.canonical_product_id or b.canonical_product_id
        if canonical_id is None:
            canonical = CanonicalProduct(
                organisation_id=organisation_id,
                gtin=a.gtin or b.gtin,
                brand=a.brand or b.brand,
                model_number=a.mpn or b.mpn,
                normalised_title=normalise_title(a.title),
                match_status="verified",
            )
            db.add(canonical)
            db.flush()
            canonical_id = canonical.id
        # Listings stay distinct records; they are only linked to the canonical product.
        for listing in (a, b):
            if listing.canonical_product_id not in (None, canonical_id):
                db.query(Listing).filter(Listing.canonical_product_id == listing.canonical_product_id).update(
                    {Listing.canonical_product_id: canonical_id}
                )
            listing.canonical_product_id = canonical_id
    return match
