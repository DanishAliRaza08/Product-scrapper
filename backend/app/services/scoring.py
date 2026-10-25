"""Configurable 0-100 opportunity score.

Each component is scored 0-100 from recorded data and weighted. Missing components
are imputed *conservatively* (never above the available evidence and capped at 40),
so gaps lower the score and the reported confidence instead of inflating it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import mean, pstdev

DEFAULT_WEIGHTS: dict[str, float] = {
    "demand": 0.25,
    "competition": 0.15,
    "profit_margin": 0.20,
    "price_stability": 0.10,
    "listing_quality": 0.10,
    "stock_status": 0.05,
    "supplier_reliability": 0.05,
    "delivery_speed": 0.05,
    "compliance_risk": 0.05,
}
CONSERVATIVE_PRIOR_CAP = 40.0


@dataclass
class Component:
    name: str
    weight: float
    score: float | None
    evidence: str


@dataclass
class OpportunityScore:
    score: float
    confidence: float
    components: list[Component] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def demand_score(sales_rank: int | None, estimated_monthly_units: Decimal | None) -> Component:
    if estimated_monthly_units is not None:
        units = float(estimated_monthly_units)
        s = _clamp(math.log10(units + 1) / math.log10(1001) * 100)  # 1000+/month -> 100
        return Component("demand", 0, s, f"~{units:.0f} units/month (estimated)")
    if sales_rank:
        s = _clamp((1 - math.log10(sales_rank) / 6) * 100)  # rank 1 -> 100, rank 1,000,000 -> 0
        return Component("demand", 0, s, f"Sales rank {sales_rank:,}")
    return Component("demand", 0, None, "No rank or demand estimate available")


def competition_score(review_count: int | None, offer_count: int | None, matched_listings: int | None) -> Component:
    parts = []
    if review_count is not None:
        parts.append(100 - _clamp(math.log10(review_count + 1) * 25))  # 10k reviews -> 0
    if offer_count is not None:
        parts.append(100 - _clamp(offer_count * 5))
    if matched_listings is not None:
        parts.append(100 - _clamp(matched_listings * 10))
    if not parts:
        return Component("competition", 0, None, "No competition signals recorded")
    return Component(
        "competition",
        0,
        mean(parts),
        ", ".join(
            f"{name} {value:,}"
            for name, value in (
                ("reviews", review_count),
                ("offers", offer_count),
                ("cross-market listings", matched_listings),
            )
            if value is not None
        ),
    )


def margin_score(margin_pct: Decimal | None) -> Component:
    if margin_pct is None:
        return Component("profit_margin", 0, None, "Needs supplier cost and a fee profile")
    return Component("profit_margin", 0, _clamp(float(margin_pct) * 2.5), f"Estimated margin {margin_pct}%")


def stability_score(prices: list[Decimal]) -> Component:
    values = [float(p) for p in prices if p is not None]
    if len(values) < 3:
        return Component("price_stability", 0, None, f"{len(values)} price observations (need 3+)")
    avg = mean(values)
    cv = pstdev(values) / avg if avg else 1
    return Component(
        "price_stability", 0, _clamp(100 - cv * 500), f"Price variation {cv * 100:.1f}% over {len(values)} obs"
    )


def quality_score(rating: Decimal | None, image_count: int, has_description: bool) -> Component:
    if rating is None and image_count == 0 and not has_description:
        return Component("listing_quality", 0, None, "No listing quality signals")
    parts = [min(image_count, 6) / 6 * 100, 100.0 if has_description else 30.0]
    if rating is not None:
        parts.append(_clamp((float(rating) - 3) / 2 * 100))
    return Component("listing_quality", 0, mean(parts), f"rating={rating}, images={image_count}")


def stock_score(stock_status: str | None) -> Component:
    mapping = {"in_stock": 100.0, "low_stock": 70.0, "preorder": 50.0, "out_of_stock": 20.0}
    if stock_status not in mapping:
        return Component("stock_status", 0, None, "Stock status unknown")
    return Component("stock_status", 0, mapping[stock_status], stock_status.replace("_", " "))


def user_component(name: str, value: float | None, invert: bool = False) -> Component:
    if value is None:
        return Component(name, 0, None, "Not provided")
    v = _clamp(float(value))
    return Component(name, 0, 100 - v if invert else v, f"User-provided {value}")


def combine(components: list[Component], weights: dict[str, float] | None = None) -> OpportunityScore:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    for c in components:
        c.weight = float(weights.get(c.name, 0))
    total_weight = sum(c.weight for c in components) or 1.0
    available = [c for c in components if c.score is not None and c.weight > 0]
    missing = [c.name for c in components if c.score is None and c.weight > 0]
    if not available:
        return OpportunityScore(0.0, 0.0, components, missing)
    avail_weight = sum(c.weight for c in available)
    raw = sum(c.score * c.weight for c in available) / avail_weight  # type: ignore[operator]
    prior = min(raw, CONSERVATIVE_PRIOR_CAP)
    score = (raw * avail_weight + prior * (total_weight - avail_weight)) / total_weight
    return OpportunityScore(round(score, 1), round(avail_weight / total_weight, 2), components, missing)
