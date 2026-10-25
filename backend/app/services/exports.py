"""Background Excel/CSV exports.

* Streams rows from a server-side cursor in chunks (constant memory).
* Excel uses openpyxl write-only mode; CSV streams directly.
* Untrusted text is neutralised against spreadsheet formula injection.
* Each file includes a manifest (filters, scope, timestamp, row counts).
* Files are stored privately and downloaded through short-lived signed links.
"""

from __future__ import annotations

import csv
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.security import constant_time_equals, sign
from app.core.storage import get_storage
from app.models import Export, Image, Listing, Marketplace, Observation, ProductMetric, User, Variation
from app.services import analysis
from app.services.products import ProductFilters, build_query

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")
CHUNK = 2000
ALL_SHEETS = ["Products", "Variations", "Images", "Price History", "Metrics", "Profit Analysis"]
CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


def safe_cell(value: Any) -> Any:
    """Neutralise values that a spreadsheet would interpret as a formula."""
    if isinstance(value, str):
        if value.startswith(FORMULA_PREFIXES):
            return "'" + value
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, dict)):
        return safe_cell(str(value))
    return value


PRODUCT_COLUMNS = [
    ("Listing ID", "id"),
    ("Marketplace", "marketplace"),
    ("Country", "country"),
    ("External ID", "external_listing_id"),
    ("SKU", "seller_sku"),
    ("ASIN", "asin"),
    ("GTIN", "gtin"),
    ("MPN", "mpn"),
    ("Title", "title"),
    ("Brand", "brand"),
    ("Category", "category_path"),
    ("Description", "description"),
    ("Price", "current_price"),
    ("Currency", "currency_code"),
    ("Shipping", "shipping_price"),
    ("Availability", "stock_status"),
    ("Stock qty", "stock_quantity"),
    ("Stock qty type", "stock_quantity_type"),
    ("Rating", "rating"),
    ("Reviews", "review_count"),
    ("Sales rank", "sales_rank"),
    ("Rank category", "rank_category"),
    ("Seller", "seller_name"),
    ("URL", "product_url"),
    ("Main image", "main_image_url"),
    ("Source type", "source_type"),
    ("Last seen (UTC)", "last_seen_at"),
]


def _id_chunks(db: Session, stmt, limit: int) -> Iterator[list[int]]:
    """Walk the filtered listing IDs in keyset order without materialising them all."""
    last, emitted = 0, 0
    base = stmt.with_only_columns(Listing.id).order_by(None).order_by(Listing.id)
    while emitted < limit:
        chunk = list(db.scalars(base.where(Listing.id > last).limit(min(CHUNK, limit - emitted))))
        if not chunk:
            return
        yield chunk
        last = chunk[-1]
        emitted += len(chunk)


def _product_rows(db: Session, stmt, limit: int) -> Iterator[tuple[Listing, Marketplace]]:
    for chunk in _id_chunks(db, stmt, limit):
        query = (
            select(Listing, Marketplace)
            .join(Marketplace, Marketplace.id == Listing.marketplace_id)
            .where(Listing.id.in_(chunk))
            .order_by(Listing.id)
        )
        yield from db.execute(query).tuples()
        db.expunge_all()  # keep the identity map (and memory) bounded


def generate(export_id: uuid.UUID) -> None:
    settings = get_settings()
    with session_scope() as db:
        export = db.get(Export, export_id)
        assert export is not None
        export.export_status = "running"
        filters = ProductFilters.model_validate(export.filters or {})
        user = db.get(User, export.user_id) if export.user_id else None
        organisation_id = export.organisation_id
        sheets = [s for s in (export.sheets or ALL_SHEETS) if s in ALL_SHEETS]
        fmt = export.export_format
        row_limit = user.export_row_limit if user and user.export_row_limit else 1_000_000

    tmpdir = Path(tempfile.mkdtemp(prefix="mpi-export-"))
    path = tmpdir / f"export.{fmt}"
    counts: dict[str, int] = {}
    try:
        with session_scope() as db:
            from app.core.security import Permission, has_permission

            can_view_own = bool(user and has_permission(user.role, Permission.VIEW_OWN_ACCOUNT_DATA))
            stmt = build_query(organisation_id, filters, can_view_own=can_view_own)
            includes_own = (
                can_view_own
                and filters.include_own_account
                and bool(
                    db.scalar(stmt.with_only_columns(Listing.id).where(Listing.source_type == "own_account").limit(1))
                )
            )
            if fmt == "csv":
                counts["Products"] = _write_csv(db, stmt, row_limit, path)
            else:
                counts = _write_xlsx(db, stmt, row_limit, organisation_id, sheets, path)
        manifest = {
            "generated_at": datetime.now(UTC).isoformat(),
            "filters": filters.model_dump(mode="json", exclude_none=True),
            "format": fmt,
            "sheets": counts,
            "row_limit": row_limit,
            "includes_own_account_data": includes_own,
            "notes": [
                "Metrics are labelled actual / estimated / unavailable. Unavailable values are blank, never zero.",
                "Prices are in the listing's original currency.",
            ],
        }
        key = f"exports/{organisation_id}/{export_id}.{fmt}"
        get_storage().put_file(key, path, CONTENT_TYPES[fmt])
        with session_scope() as db:
            db.execute(
                update(Export)
                .where(Export.id == export_id)
                .values(
                    export_status="completed",
                    storage_key=key,
                    file_size_bytes=path.stat().st_size,
                    row_count=counts.get("Products", 0),
                    progress=100,
                    manifest=manifest,
                    includes_own_account_data=includes_own,
                    completed_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC) + timedelta(days=settings.export_retention_days),
                )
            )
    except Exception as exc:
        with session_scope() as db:
            db.execute(
                update(Export)
                .where(Export.id == export_id)
                .values(export_status="failed", error_summary=f"{type(exc).__name__}: {str(exc)[:300]}")
            )
        raise
    finally:
        path.unlink(missing_ok=True)
        tmpdir.rmdir()


def _product_values(listing: Listing, marketplace: Marketplace) -> list[Any]:
    out = []
    for _, attr in PRODUCT_COLUMNS:
        if attr == "marketplace":
            out.append(marketplace.code)
        elif attr == "country":
            out.append(marketplace.country_code)
        else:
            out.append(getattr(listing, attr))
    return [safe_cell(v) for v in out]


def _write_csv(db: Session, stmt, limit: int, path: Path) -> int:
    n = 0
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([c for c, _ in PRODUCT_COLUMNS])
        for listing, marketplace in _product_rows(db, stmt, limit):
            writer.writerow(["" if v is None else v for v in _product_values(listing, marketplace)])
            n += 1
    return n


def _write_xlsx(db: Session, stmt, limit: int, organisation_id: uuid.UUID, sheets: list[str], path: Path) -> dict:
    wb = Workbook(write_only=True)
    counts: dict[str, int] = {}

    if "Products" in sheets:
        ws = wb.create_sheet("Products")
        ws.append([c for c, _ in PRODUCT_COLUMNS])
        counts["Products"] = 0
        for listing, marketplace in _product_rows(db, stmt, limit):
            ws.append(_product_values(listing, marketplace))
            counts["Products"] += 1

    if "Variations" in sheets:
        ws = wb.create_sheet("Variations")
        ws.append(
            [
                "Listing ID",
                "Parent external ID",
                "Variation ID",
                "SKU",
                "Size",
                "Colour",
                "Price",
                "Currency",
                "Availability",
            ]
        )
        counts["Variations"] = 0
        for chunk in _id_chunks(db, stmt, limit):
            for v, ext in db.execute(
                select(Variation, Listing.external_listing_id)
                .join(Listing, Listing.id == Variation.listing_id)
                .where(Variation.listing_id.in_(chunk))
                .order_by(Variation.listing_id, Variation.id)
            ):
                ws.append(
                    [
                        safe_cell(x)
                        for x in (
                            v.listing_id,
                            ext,
                            v.external_variation_id,
                            v.variation_sku,
                            v.size,
                            v.colour,
                            v.price,
                            v.currency_code,
                            v.stock_status,
                        )
                    ]
                )
                counts["Variations"] += 1

    if "Images" in sheets:
        ws = wb.create_sheet("Images")
        ws.append(["Listing ID", "Image URL", "Image type", "Order"])
        counts["Images"] = 0
        for chunk in _id_chunks(db, stmt, limit):
            for img in db.scalars(
                select(Image).where(Image.listing_id.in_(chunk)).order_by(Image.listing_id, Image.display_order)
            ):
                ws.append([safe_cell(x) for x in (img.listing_id, img.image_url, img.image_type, img.display_order)])
                counts["Images"] += 1

    if "Price History" in sheets:
        ws = wb.create_sheet("Price History")
        ws.append(["Listing ID", "Observed at (UTC)", "Price", "Currency", "Shipping", "Availability", "Sales rank"])
        counts["Price History"] = 0
        for chunk in _id_chunks(db, stmt, limit):
            for o in db.scalars(
                select(Observation)
                .where(Observation.listing_id.in_(chunk))
                .order_by(Observation.listing_id, Observation.observed_at)
            ):
                ws.append(
                    [
                        safe_cell(x)
                        for x in (
                            o.listing_id,
                            o.observed_at,
                            o.price,
                            o.currency_code,
                            o.shipping_price,
                            o.stock_status,
                            o.sales_rank,
                        )
                    ]
                )
                counts["Price History"] += 1

    if "Metrics" in sheets:
        ws = wb.create_sheet("Metrics")
        ws.append(
            [
                "Listing ID",
                "Metric",
                "Value",
                "Unit",
                "Measurement type",
                "Methodology",
                "Confidence",
                "Source",
                "Observed at (UTC)",
            ]
        )
        counts["Metrics"] = 0
        for chunk in _id_chunks(db, stmt, limit):
            for m in db.scalars(
                select(ProductMetric)
                .where(ProductMetric.listing_id.in_(chunk))
                .order_by(ProductMetric.listing_id, ProductMetric.observed_at)
            ):
                ws.append(
                    [
                        safe_cell(x)
                        for x in (
                            m.listing_id,
                            m.metric_name,
                            m.numeric_value,
                            m.unit,
                            m.measurement_type,
                            m.methodology,
                            m.confidence,
                            m.source,
                            m.observed_at,
                        )
                    ]
                )
                counts["Metrics"] += 1

    if "Profit Analysis" in sheets:
        ws = wb.create_sheet("Profit Analysis")
        ws.append(
            [
                "Listing ID",
                "Currency",
                "Selling price",
                "Net revenue",
                "Tax",
                "Marketplace fees",
                "Payment fees",
                "Returns allowance",
                "Total cost",
                "Profit",
                "Margin %",
                "ROI %",
                "Break-even price",
                "Complete",
                "Missing inputs",
            ]
        )
        counts["Profit Analysis"] = 0
        for chunk in _id_chunks(db, stmt, limit):
            for listing in db.scalars(select(Listing).where(Listing.id.in_(chunk)).order_by(Listing.id)):
                r = analysis.listing_profit(db, organisation_id, listing)
                ws.append(
                    [
                        safe_cell(x)
                        for x in (
                            listing.id,
                            r.currency_code,
                            r.selling_price,
                            r.net_revenue,
                            r.tax_amount,
                            r.marketplace_fees,
                            r.payment_fees,
                            r.returns_allowance,
                            r.total_cost,
                            r.profit,
                            r.margin_pct,
                            r.roi_pct,
                            r.break_even_price,
                            "yes" if r.is_complete else "no",
                            ", ".join(r.missing_inputs),
                        )
                    ]
                )
                counts["Profit Analysis"] += 1

    ws = wb.create_sheet("Manifest")
    ws.append(["Key", "Value"])
    ws.append(["Generated at (UTC)", safe_cell(datetime.now(UTC))])
    ws.append(["Listings", counts.get("Products", 0)])
    for name, n in counts.items():
        ws.append([f"Rows: {name}", n])
    ws.append(["Note", "Estimated metrics are labelled; unavailable metrics are blank, never zero."])
    wb.save(path)
    return counts


# ----------------------------------------------------------- signed links
def download_token(export_id: uuid.UUID, user_id: uuid.UUID, expires: datetime) -> str:
    payload = f"{export_id}:{user_id}:{int(expires.timestamp())}"
    return f"{int(expires.timestamp())}.{sign(payload, get_settings().jwt_secret.get_secret_value())}"


def verify_download_token(export_id: uuid.UUID, user_id: uuid.UUID, token: str) -> bool:
    try:
        ts, mac = token.split(".", 1)
        expires = int(ts)
    except ValueError:
        return False
    if expires < datetime.now(UTC).timestamp():
        return False
    expected = sign(f"{export_id}:{user_id}:{expires}", get_settings().jwt_secret.get_secret_value())
    return constant_time_equals(mac, expected)


def expire_old_exports() -> int:
    with session_scope() as db:
        rows = db.scalars(
            select(Export).where(Export.export_status == "completed", Export.expires_at < datetime.now(UTC))
        ).all()
        for e in rows:
            if e.storage_key:
                get_storage().delete(e.storage_key)
            e.export_status = "expired"
            e.storage_key = None
        return len(rows)
