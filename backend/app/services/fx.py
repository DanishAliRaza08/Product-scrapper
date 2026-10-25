"""Dated currency conversion. Original amounts are always kept; conversions use the
latest rate effective at (or before) the requested time and report which rate was used."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 - parses a fixed, trusted ECB feed
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import ExchangeRate

ECB_DAILY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


@dataclass
class RateUsed:
    rate: Decimal
    source: str
    effective_at: datetime
    inverted: bool = False


def find_rate(db: Session, base: str, quote: str, at: datetime | None = None) -> RateUsed | None:
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return RateUsed(Decimal(1), "identity", at or datetime.now(UTC))
    at = at or datetime.now(UTC)
    for b, q, inverted in ((base, quote, False), (quote, base, True)):
        row = db.execute(
            select(ExchangeRate)
            .where(ExchangeRate.base_currency == b, ExchangeRate.quote_currency == q, ExchangeRate.effective_at <= at)
            .order_by(ExchangeRate.effective_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            rate = (Decimal(1) / row.rate) if inverted else row.rate
            return RateUsed(rate, row.source, row.effective_at, inverted)
    # Triangulate through EUR (the ECB publishes EUR-based rates).
    if "EUR" not in (base, quote):
        to_eur = find_rate(db, base, "EUR", at)
        from_eur = find_rate(db, "EUR", quote, at)
        if to_eur and from_eur:
            return RateUsed(
                to_eur.rate * from_eur.rate, f"{to_eur.source} via EUR", min(to_eur.effective_at, from_eur.effective_at)
            )
    return None


def convert(db: Session, amount: Decimal | None, base: str, quote: str, at: datetime | None = None) -> Decimal | None:
    if amount is None:
        return None
    rate = find_rate(db, base, quote, at)
    if rate is None:
        return None
    return (amount * rate.rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def store_rate(db: Session, source: str, base: str, quote: str, rate: Decimal, effective_at: datetime) -> None:
    stmt = pg_insert(ExchangeRate.__table__).values(
        source=source, base_currency=base.upper(), quote_currency=quote.upper(), rate=rate, effective_at=effective_at
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["source", "base_currency", "quote_currency", "effective_at"],
            set_={"rate": stmt.excluded.rate},
        )
    )


def parse_ecb(xml_text: str) -> tuple[datetime, dict[str, Decimal]]:
    root = ET.fromstring(xml_text)  # noqa: S314
    ns = {"e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    day = root.find(".//e:Cube/e:Cube[@time]", ns)
    if day is None:
        raise ValueError("Unexpected ECB feed format")
    effective = datetime.fromisoformat(day.attrib["time"]).replace(tzinfo=UTC)
    rates = {c.attrib["currency"]: Decimal(c.attrib["rate"]) for c in day.findall("e:Cube", ns)}
    return effective, rates


def refresh_ecb_rates(db: Session, client: httpx.Client | None = None) -> int:
    client = client or httpx.Client(timeout=20)
    response = client.get(ECB_DAILY)
    response.raise_for_status()
    effective, rates = parse_ecb(response.text)
    for currency, rate in rates.items():
        store_rate(db, "ECB", "EUR", currency, rate, effective)
    return len(rates)
