"""Initial schema (see migrations/sql/0001_initial.sql).

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-25
"""

from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0001_initial.sql"

TABLES = [
    "connector_health",
    "audit_logs",
    "notifications",
    "alert_rules",
    "exports",
    "exchange_rates",
    "product_costs",
    "fee_profiles",
    "product_metrics",
    "listing_observations",
    "job_events",
    "extraction_job_items",
    "extraction_jobs",
    "saved_searches",
    "project_products",
    "competitor_shops",
    "research_projects",
    "seller_order_items",
    "seller_orders",
    "product_matches",
    "product_sources",
    "product_images",
    "product_variations",
    "marketplace_listings",
    "canonical_products",
    "sync_checkpoints",
    "oauth_states",
    "marketplace_accounts",
    "marketplaces",
    "refresh_tokens",
    "users",
    "organisations",
]


def _execute_raw(sql: str) -> None:
    # Run through the DBAPI cursor without parameters so '%' and ':' in PL/pgSQL
    # bodies are never treated as bind placeholders.
    cursor = op.get_bind().connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def upgrade() -> None:
    _execute_raw(SQL_FILE.read_text())


def downgrade() -> None:
    _execute_raw("DROP MATERIALIZED VIEW IF EXISTS mv_listing_summary;")
    _execute_raw("DROP FUNCTION IF EXISTS ensure_observation_partitions(INTEGER);")
    for table in TABLES:
        _execute_raw(f"DROP TABLE IF EXISTS {table} CASCADE;")
