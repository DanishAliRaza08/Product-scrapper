export type Page<T> = { items: T[]; next_cursor: string | null; total: number | null; total_is_estimate: boolean };

export type Session = {
  access_token: string;
  expires_in: number;
  refresh_token: string | null;
  user: { id: string; email: string; full_name: string | null; role: string; organisation_id: string };
  organisation_name: string;
  permissions: string[];
};

export type Marketplace = {
  id: number; code: string; platform: string; name: string; country_code: string; currency_code: string;
  prices_include_tax: boolean;
};

export type Listing = {
  id: number; marketplace_code: string; country_code: string; external_listing_id: string; seller_sku: string | null;
  title: string; brand: string | null; category_path: string | null; main_image_url: string | null; product_url: string;
  currency_code: string | null; current_price: string | null; shipping_price: string | null; stock_status: string | null;
  rating: string | null; review_count: number | null; sales_rank: number | null; source_type: string;
  is_watched: boolean; canonical_product_id: number | null; last_seen_at: string; last_changed_at: string | null;
};

export type ListingDetail = Listing & {
  parent_listing_id: string | null; asin: string | null; gtin: string | null; mpn: string | null;
  description: string | null; original_price: string | null; stock_quantity: number | null;
  stock_quantity_type: string | null; condition: string | null; rank_category: string | null;
  seller_name: string | null; seller_external_id: string | null; fulfilment_type: string | null;
  attributes: Record<string, unknown>; first_seen_at: string;
  variations: { id: number; external_variation_id: string | null; variation_sku: string | null; size: string | null;
    colour: string | null; price: string | null; currency_code: string | null; stock_status: string | null }[];
  images: { id: number; image_url: string; image_type: string | null; display_order: number }[];
};

export type Job = {
  id: string; job_type: string; input_type: string; marketplace_code: string | null; input_url: string | null;
  search_keyword: string | null; category: string | null; job_status: string; project_id: string | null;
  products_discovered: number; products_processed: number; products_failed: number; products_skipped: number;
  duplicates: number; discovery_complete: boolean; source_limit_note: string | null; error_summary: string | null;
  progress_pct: number | null; configuration: Record<string, unknown>; created_at: string; started_at: string | null;
  completed_at: string | null;
};

export type JobEvent = { id: number; event_type: string; level: string; message: string; created_at: string };

export type Metric = {
  metric_name: string; numeric_value: string | null; unit: string | null;
  measurement_type: "actual" | "estimated" | "unavailable"; methodology: string | null; confidence: string | null;
  source: string; observed_at: string;
};

export type Observation = {
  observed_at: string; price: string | null; currency_code: string | null; stock_status: string | null;
  sales_rank: number | null; rating: string | null; review_count: number | null;
};

export type Profit = {
  currency_code: string; selling_price: string | null; net_revenue: string | null; tax_amount: string | null;
  marketplace_fees: string | null; payment_fees: string | null; returns_allowance: string | null;
  total_cost: string | null; profit: string | null; margin_pct: string | null; roi_pct: string | null;
  break_even_price: string | null; target_margin_price: string | null; is_complete: boolean;
  missing_inputs: string[]; assumptions: string[]; fx: { from: string; to: string; rate: string; source: string }[];
};

export type Opportunity = {
  score: number; confidence: number; missing: string[];
  components: { name: string; weight: number; score: number | null; evidence: string }[];
};

export type Project = { id: string; name: string; description: string | null; product_count: number; created_at: string };

export type Connector = {
  key: string; display_name: string; release_status: string; marketplaces: string[]; supported_inputs: string[];
  own_account_streams: string[]; fields_available: string[]; known_missing_fields: string[];
  metric_provenance: Record<string, string>; limitations: string[]; configured: boolean;
  configuration_hint: string | null; data_licence_note: string; max_results_per_query: number | null;
  rate_limit: { requests_per_second: number }; health: string | null;
};

export type Match = {
  id: number; source_listing_id: number; target_listing_id: number; match_method: string; score: string;
  evidence: Record<string, unknown>; status: string; source: Listing; target: Listing;
};

export type ExportJob = {
  id: string; export_format: string; export_status: string; row_count: number | null; file_size_bytes: number | null;
  error_summary: string | null; created_at: string; completed_at: string | null; expires_at: string | null;
  includes_own_account_data: boolean; manifest: Record<string, unknown> | null;
};
