-- Multi-Marketplace Product Intelligence: initial schema.
-- Conventions: UUID keys for tenant-level entities, BIGINT identity keys for
-- high-volume tables, TIMESTAMPTZ everywhere, NUMERIC(14,4) for money, and every
-- monetary value carries its own currency code.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- tenancy
CREATE TABLE organisations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    base_currency CHAR(3) NOT NULL DEFAULT 'GBP',
    settings JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    full_name TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'researcher'
        CHECK (role IN ('admin', 'research_manager', 'researcher', 'viewer')),
    is_active BOOLEAN NOT NULL DEFAULT true,
    export_row_limit INTEGER,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organisation_id, email)
);
CREATE UNIQUE INDEX uq_users_email_ci ON users (lower(email));

CREATE TABLE refresh_tokens (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    family_id UUID NOT NULL,
    client_type TEXT NOT NULL DEFAULT 'web',
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    replaced_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_refresh_tokens_user ON refresh_tokens (user_id);
CREATE INDEX idx_refresh_tokens_family ON refresh_tokens (family_id);

-- ------------------------------------------------------------ marketplaces
CREATE TABLE marketplaces (
    id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    country_code CHAR(2) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    prices_include_tax BOOLEAN NOT NULL DEFAULT false,
    default_tax_rate NUMERIC(6,4) NOT NULL DEFAULT 0,
    is_enabled BOOLEAN NOT NULL DEFAULT true
);

INSERT INTO marketplaces (code, platform, name, country_code, currency_code, prices_include_tax, default_tax_rate) VALUES
    ('AMAZON_US', 'amazon', 'Amazon.com', 'US', 'USD', false, 0),
    ('AMAZON_UK', 'amazon', 'Amazon.co.uk', 'GB', 'GBP', true, 0.20),
    ('EBAY_US', 'ebay', 'eBay US', 'US', 'USD', false, 0),
    ('EBAY_UK', 'ebay', 'eBay UK', 'GB', 'GBP', true, 0.20),
    ('TIKTOK_US', 'tiktok', 'TikTok Shop US', 'US', 'USD', false, 0),
    ('TIKTOK_UK', 'tiktok', 'TikTok Shop UK', 'GB', 'GBP', true, 0.20),
    ('ALIEXPRESS_US', 'aliexpress', 'AliExpress (ship to US)', 'US', 'USD', false, 0),
    ('ALIEXPRESS_UK', 'aliexpress', 'AliExpress (ship to UK)', 'GB', 'GBP', true, 0.20),
    ('DEMO_US', 'demo', 'Demo Marketplace US (synthetic data)', 'US', 'USD', false, 0),
    ('DEMO_UK', 'demo', 'Demo Marketplace UK (synthetic data)', 'GB', 'GBP', true, 0.20);

-- ------------------------------------------------------ seller accounts
CREATE TABLE marketplace_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    marketplace_id SMALLINT NOT NULL REFERENCES marketplaces(id),
    account_name TEXT NOT NULL,
    external_account_id TEXT,
    connection_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (connection_status IN ('pending', 'connected', 'expired', 'revoked', 'error')),
    granted_scopes TEXT[] NOT NULL DEFAULT '{}',
    credential_reference TEXT,
    last_sync_at TIMESTAMPTZ,
    last_error TEXT,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_accounts_org ON marketplace_accounts (organisation_id);

CREATE TABLE oauth_states (
    state TEXT PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES marketplace_accounts(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE sync_checkpoints (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES marketplace_accounts(id) ON DELETE CASCADE,
    stream TEXT NOT NULL,
    cursor TEXT,
    last_successful_sync_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE (account_id, stream)
);

-- --------------------------------------------------- products & listings
CREATE TABLE canonical_products (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    gtin TEXT,
    brand TEXT,
    model_number TEXT,
    normalised_title TEXT,
    attributes JSONB NOT NULL DEFAULT '{}',
    match_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (match_status IN ('unverified', 'verified', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_canonical_gtin ON canonical_products (organisation_id, gtin);
CREATE INDEX idx_canonical_attributes ON canonical_products USING GIN (attributes);

-- Listings are tenant-scoped: own-account data must never leak between
-- organisations, and competitor research is attributable to whoever ran it.
CREATE TABLE marketplace_listings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    marketplace_id SMALLINT NOT NULL REFERENCES marketplaces(id),
    canonical_product_id BIGINT REFERENCES canonical_products(id) ON DELETE SET NULL,
    account_id UUID REFERENCES marketplace_accounts(id) ON DELETE SET NULL,
    external_listing_id TEXT NOT NULL,
    parent_listing_id TEXT,
    seller_sku TEXT,
    asin TEXT,
    gtin TEXT,
    mpn TEXT,
    title TEXT NOT NULL,
    description TEXT,
    brand TEXT,
    category_path TEXT,
    category_id TEXT,
    product_url TEXT NOT NULL,
    main_image_url TEXT,
    currency_code CHAR(3),
    current_price NUMERIC(14,4),
    original_price NUMERIC(14,4),
    shipping_price NUMERIC(14,4),
    stock_status TEXT CHECK (stock_status IN ('in_stock', 'low_stock', 'out_of_stock', 'preorder', 'unknown')),
    stock_quantity INTEGER,
    stock_quantity_type TEXT CHECK (stock_quantity_type IN ('actual', 'estimated', 'unavailable')),
    condition TEXT,
    rating NUMERIC(3,2),
    review_count INTEGER,
    sales_rank BIGINT,
    rank_category TEXT,
    seller_external_id TEXT,
    seller_name TEXT,
    fulfilment_type TEXT,
    attributes JSONB NOT NULL DEFAULT '{}',
    source_type TEXT NOT NULL
        CHECK (source_type IN ('own_account', 'competitor_research', 'partner_estimate', 'user_entered', 'derived')),
    is_watched BOOLEAN NOT NULL DEFAULT false,
    refresh_priority SMALLINT NOT NULL DEFAULT 50,
    content_hash TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_changed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_vector TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(brand, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(external_listing_id, '') || ' ' || coalesce(seller_sku, '') || ' ' || coalesce(gtin, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(category_path, '')), 'C')
    ) STORED,
    UNIQUE (organisation_id, marketplace_id, external_listing_id)
);
CREATE INDEX idx_listing_canonical ON marketplace_listings (canonical_product_id);
CREATE INDEX idx_listing_marketplace_category ON marketplace_listings (organisation_id, marketplace_id, category_path);
CREATE INDEX idx_listing_sku ON marketplace_listings (organisation_id, seller_sku);
CREATE INDEX idx_listing_gtin ON marketplace_listings (organisation_id, gtin) WHERE gtin IS NOT NULL;
CREATE INDEX idx_listing_price ON marketplace_listings (organisation_id, currency_code, current_price, id);
CREATE INDEX idx_listing_updated ON marketplace_listings (organisation_id, updated_at DESC, id DESC);
CREATE INDEX idx_listing_refresh ON marketplace_listings (organisation_id, last_seen_at) WHERE source_type <> 'own_account';
CREATE INDEX idx_listing_search ON marketplace_listings USING GIN (search_vector);
CREATE INDEX idx_listing_title_trgm ON marketplace_listings USING GIN (title gin_trgm_ops);

CREATE TABLE product_variations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    external_variation_id TEXT,
    variation_sku TEXT,
    size TEXT,
    colour TEXT,
    price NUMERIC(14,4),
    currency_code CHAR(3),
    stock_status TEXT,
    attributes JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_variation_external_id ON product_variations (listing_id, external_variation_id)
    WHERE external_variation_id IS NOT NULL;

CREATE TABLE product_images (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    variation_id BIGINT REFERENCES product_variations(id) ON DELETE SET NULL,
    image_url TEXT NOT NULL,
    storage_key TEXT,
    image_type TEXT,
    width INTEGER,
    height INTEGER,
    display_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (listing_id, image_url)
);

-- Field-level provenance: where did each value come from, and when.
CREATE TABLE product_sources (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    connector TEXT NOT NULL,
    connector_version TEXT,
    endpoint TEXT,
    source_reference TEXT,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (listing_id, field_name)
);

CREATE TABLE product_matches (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    source_listing_id BIGINT NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    target_listing_id BIGINT NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    match_method TEXT NOT NULL,
    score NUMERIC(5,4) NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_listing_id < target_listing_id),
    UNIQUE (source_listing_id, target_listing_id)
);
CREATE INDEX idx_matches_org_status ON product_matches (organisation_id, status, score DESC);

-- ------------------------------------------------------------- own orders
CREATE TABLE seller_orders (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES marketplace_accounts(id) ON DELETE CASCADE,
    external_order_id TEXT NOT NULL,
    order_status TEXT,
    order_currency CHAR(3),
    order_total NUMERIC(14,4),
    ordered_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    raw_reference TEXT,
    UNIQUE (account_id, external_order_id)
);
CREATE INDEX idx_orders_account_time ON seller_orders (account_id, ordered_at DESC);

CREATE TABLE seller_order_items (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES seller_orders(id) ON DELETE CASCADE,
    external_order_item_id TEXT,
    listing_id BIGINT REFERENCES marketplace_listings(id) ON DELETE SET NULL,
    seller_sku TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price NUMERIC(14,4),
    item_tax NUMERIC(14,4),
    marketplace_fees NUMERIC(14,4),
    refunded_amount NUMERIC(14,4) NOT NULL DEFAULT 0,
    currency_code CHAR(3),
    UNIQUE (order_id, external_order_item_id)
);
CREATE INDEX idx_order_items_listing ON seller_order_items (listing_id);

-- -------------------------------------------------------- projects
CREATE TABLE research_projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organisation_id, name)
);

CREATE TABLE competitor_shops (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
    marketplace_id SMALLINT NOT NULL REFERENCES marketplaces(id),
    shop_url TEXT NOT NULL,
    external_seller_id TEXT,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, marketplace_id, shop_url)
);

CREATE TABLE project_products (
    project_id UUID NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
    listing_id BIGINT NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    tags TEXT[] NOT NULL DEFAULT '{}',
    notes TEXT,
    added_by UUID REFERENCES users(id) ON DELETE SET NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, listing_id)
);
CREATE INDEX idx_project_products_listing ON project_products (listing_id);

CREATE TABLE saved_searches (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    project_id UUID REFERENCES research_projects(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('job_input', 'product_filter')),
    parameters JSONB NOT NULL,
    schedule_cron TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------- jobs
CREATE TABLE extraction_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    project_id UUID REFERENCES research_projects(id) ON DELETE SET NULL,
    account_id UUID REFERENCES marketplace_accounts(id) ON DELETE SET NULL,
    marketplace_id SMALLINT REFERENCES marketplaces(id),
    job_type TEXT NOT NULL DEFAULT 'research' CHECK (job_type IN ('research', 'account_sync', 'refresh')),
    input_type TEXT NOT NULL
        CHECK (input_type IN ('shop_url', 'category_url', 'product_url', 'keyword', 'category', 'listing_ids', 'account')),
    input_url TEXT,
    search_keyword TEXT,
    category TEXT,
    job_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (job_status IN ('pending', 'queued', 'running', 'completed', 'partial', 'failed',
                              'cancelling', 'cancelled', 'paused')),
    configuration JSONB NOT NULL DEFAULT '{}',
    discovery_cursor TEXT,
    discovery_complete BOOLEAN NOT NULL DEFAULT false,
    source_limit_note TEXT,
    idempotency_key TEXT,
    products_discovered BIGINT NOT NULL DEFAULT 0,
    products_processed BIGINT NOT NULL DEFAULT 0,
    products_failed BIGINT NOT NULL DEFAULT 0,
    products_skipped BIGINT NOT NULL DEFAULT 0,
    duplicates BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_summary TEXT,
    UNIQUE (organisation_id, idempotency_key)
);
CREATE INDEX idx_jobs_org_created ON extraction_jobs (organisation_id, created_at DESC);
CREATE INDEX idx_jobs_status ON extraction_jobs (job_status) WHERE job_status IN ('queued', 'running', 'cancelling');

CREATE TABLE extraction_job_items (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES extraction_jobs(id) ON DELETE CASCADE,
    external_product_id TEXT NOT NULL,
    listing_id BIGINT REFERENCES marketplace_listings(id) ON DELETE SET NULL,
    item_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (item_status IN ('pending', 'processing', 'succeeded', 'failed', 'skipped', 'unavailable')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    next_attempt_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    UNIQUE (job_id, external_product_id)
);
CREATE INDEX idx_job_items_pending ON extraction_job_items (job_id, id) WHERE item_status IN ('pending', 'processing');

CREATE TABLE job_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES extraction_jobs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_job_events_job ON job_events (job_id, id);

-- --------------------------------------------- history & metrics
CREATE TABLE listing_observations (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    listing_id BIGINT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    price NUMERIC(14,4),
    currency_code CHAR(3),
    shipping_price NUMERIC(14,4),
    stock_status TEXT,
    stock_quantity INTEGER,
    sales_rank BIGINT,
    rank_category TEXT,
    rating NUMERIC(3,2),
    review_count INTEGER,
    job_id UUID,
    source_reference TEXT,
    PRIMARY KEY (id, observed_at)
) PARTITION BY RANGE (observed_at);
-- No FK to listings: FKs from partitioned tables are supported in PG12+, but a
-- cascade across hundreds of millions of rows is undesirable. Retention jobs
-- remove orphans in bulk instead.
CREATE INDEX idx_observations_listing_time ON listing_observations (listing_id, observed_at DESC);
CREATE TABLE listing_observations_default PARTITION OF listing_observations DEFAULT;

CREATE OR REPLACE FUNCTION ensure_observation_partitions(months_ahead INTEGER DEFAULT 3)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    start_month DATE := date_trunc('month', now() - interval '1 month')::date;
    m DATE;
    part TEXT;
    created INTEGER := 0;
BEGIN
    FOR i IN 0..months_ahead + 1 LOOP
        m := (start_month + make_interval(months => i))::date;
        part := format('listing_observations_%s', to_char(m, 'YYYY_MM'));
        IF to_regclass(part) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF listing_observations FOR VALUES FROM (%L) TO (%L)',
                part, m, (m + interval '1 month')::date
            );
            created := created + 1;
        END IF;
    END LOOP;
    RETURN created;
END;
$$;
SELECT ensure_observation_partitions(3);

CREATE TABLE product_metrics (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    numeric_value NUMERIC(18,4),
    unit TEXT,
    measurement_type TEXT NOT NULL CHECK (measurement_type IN ('actual', 'estimated', 'unavailable')),
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    methodology TEXT,
    confidence NUMERIC(4,3) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 'unavailable' metrics never carry a value: zero is not a substitute for unknown.
    CHECK (measurement_type <> 'unavailable' OR numeric_value IS NULL)
);
CREATE INDEX idx_metrics_listing ON product_metrics (listing_id, metric_name, observed_at DESC);

-- --------------------------------------------------- costs & fx
CREATE TABLE fee_profiles (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID REFERENCES organisations(id) ON DELETE CASCADE,
    marketplace_id SMALLINT NOT NULL REFERENCES marketplaces(id),
    name TEXT NOT NULL,
    category_pattern TEXT,
    referral_rate NUMERIC(6,4) NOT NULL DEFAULT 0,
    fixed_fee NUMERIC(14,4) NOT NULL DEFAULT 0,
    payment_rate NUMERIC(6,4) NOT NULL DEFAULT 0,
    payment_fixed_fee NUMERIC(14,4) NOT NULL DEFAULT 0,
    fulfilment_fee NUMERIC(14,4) NOT NULL DEFAULT 0,
    currency_code CHAR(3) NOT NULL,
    notes TEXT,
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fee_profiles_lookup ON fee_profiles (marketplace_id, organisation_id, effective_from DESC);

CREATE TABLE product_costs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    listing_id BIGINT REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    canonical_product_id BIGINT REFERENCES canonical_products(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    currency_code CHAR(3) NOT NULL,
    supplier_cost NUMERIC(14,4) NOT NULL DEFAULT 0,
    shipping_cost NUMERIC(14,4) NOT NULL DEFAULT 0,
    marketplace_fees NUMERIC(14,4),
    advertising_cost NUMERIC(14,4) NOT NULL DEFAULT 0,
    packaging_cost NUMERIC(14,4) NOT NULL DEFAULT 0,
    returns_allowance_rate NUMERIC(6,4) NOT NULL DEFAULT 0,
    other_costs NUMERIC(14,4) NOT NULL DEFAULT 0,
    tax_rate NUMERIC(6,4),
    tax_included_in_price BOOLEAN,
    fee_profile_id BIGINT REFERENCES fee_profiles(id) ON DELETE SET NULL,
    -- User-entered 0-100 assessments used by opportunity scoring:
    -- supplier_reliability, delivery_speed, compliance_risk.
    assessment JSONB NOT NULL DEFAULT '{}',
    notes TEXT,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (listing_id IS NOT NULL OR canonical_product_id IS NOT NULL)
);
CREATE INDEX idx_costs_listing ON product_costs (organisation_id, listing_id, effective_from DESC);

CREATE TABLE exchange_rates (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    base_currency CHAR(3) NOT NULL,
    quote_currency CHAR(3) NOT NULL,
    rate NUMERIC(18,8) NOT NULL CHECK (rate > 0),
    effective_at TIMESTAMPTZ NOT NULL,
    UNIQUE (source, base_currency, quote_currency, effective_at)
);
CREATE INDEX idx_fx_lookup ON exchange_rates (base_currency, quote_currency, effective_at DESC);

-- ------------------------------------------- exports, alerts, audit
CREATE TABLE exports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    job_id UUID REFERENCES extraction_jobs(id) ON DELETE SET NULL,
    export_format TEXT NOT NULL CHECK (export_format IN ('xlsx', 'csv')),
    export_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (export_status IN ('pending', 'running', 'completed', 'failed', 'expired')),
    filters JSONB NOT NULL DEFAULT '{}',
    sheets TEXT[] NOT NULL DEFAULT '{}',
    includes_own_account_data BOOLEAN NOT NULL DEFAULT false,
    storage_key TEXT,
    file_size_bytes BIGINT,
    row_count BIGINT,
    progress NUMERIC(5,2) NOT NULL DEFAULT 0,
    error_summary TEXT,
    manifest JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);
CREATE INDEX idx_exports_org ON exports (organisation_id, created_at DESC);

CREATE TABLE alert_rules (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID REFERENCES research_projects(id) ON DELETE CASCADE,
    listing_id BIGINT REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL CHECK (rule_type IN ('price_drop_pct', 'price_rise_pct', 'price_below', 'back_in_stock', 'out_of_stock', 'rank_improved_pct')),
    threshold NUMERIC(14,4),
    is_enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    data JSONB NOT NULL DEFAULT '{}',
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_notifications_user ON notifications (organisation_id, user_id, created_at DESC);

CREATE TABLE audit_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id UUID REFERENCES organisations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    ip_address INET,
    user_agent TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_org_time ON audit_logs (organisation_id, created_at DESC);

CREATE TABLE connector_health (
    connector TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN ('healthy', 'degraded', 'down', 'unknown', 'not_configured')),
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    rate_limited_until TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
