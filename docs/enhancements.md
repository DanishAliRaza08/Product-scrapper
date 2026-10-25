# Enhancements beyond the specification

| Area | Enhancement | Why it matters |
|---|---|---|
| Data integrity | DB constraint: an `unavailable` metric cannot hold a value; an estimated metric must declare its methodology | Makes "never fabricate" enforceable, not just a guideline |
| Profit | Profit, margin, ROI and break-even are withheld when supplier cost or fees are unknown | Unknown cost ≠ zero cost |
| Profit | UK VAT-inclusive pricing handled explicitly (`prices_include_tax` per marketplace); fees charged on the gross price | UK margins are otherwise overstated by about 17% |
| Profit | Break-even price and price-for-target-margin, solved analytically | Answers "what must I charge?", not just "what do I make?" |
| Profit | Fee profiles per marketplace and category, effective-dated | Fees change; history stays reproducible |
| FX | Dated rates; inverse and EUR triangulation; ECB daily import; rate used is reported with each result | Auditable conversions |
| Scoring | Missing components are imputed conservatively (never above evidence, capped at 40) and reported as lower confidence | Gaps can't inflate a score |
| Matching | GTIN normalised to GTIN-14 with check-digit validation; conflicting valid GTINs score 0; human decisions are never overwritten | Fewer false matches |
| History | Observations recorded on change, plus at most one per day | About 10× less history storage for stable listings |
| Ingestion | Descriptive fields keep their last known value when a source omits them; own-account listings never downgrade to competitor | Partial endpoints don't erase good data |
| Jobs | Idempotency keys; heartbeat-based crash recovery; auth or permission errors **pause** instead of failing; source-limit notes surfaced in the UI | Operators can tell "blocked" from "broken" |
| Alerts | Price drop/rise/below, back in stock, out of stock and rank improvement rules on projects or listings | Spec 3.6 "notify users of meaningful changes" |
| Refresh | Priority: watched → recently volatile → best rank → stalest; one refresh job per org and marketplace | Spends quota where it matters |
| Scale | Row-value keyset pagination and `(org, column, id)` indexes; materialised dashboard view; capped counts | First and deep pages ~4 ms at 1M rows |
| Security | Refresh-token family revocation on reuse; desktop tokens in OS keychain; signed cursors and download links; OAuth state binding | Hardening beyond the spec's list |
| Desktop | Same UI as web via static export; native downloads and notifications; configurable API endpoint | One codebase, two clients |
| Connectors | Typed contract and honest capability manifests surfaced in the UI before a job starts | Users see limits before they run a job |

## Known limitations and next steps

* **Live connectors need credentials.** eBay and Amazon connectors follow the official
  API documentation and are tested against representative payloads. Validate them with
  sandbox and production keys before relying on them.
* **TikTok Shop and AliExpress** are declared but not implemented, pending approved partner access.
* **Cloud secrets manager:** implement the `SecretStore` protocol for your provider (`LocalSecretStore` is for single-host use).
* **Offline desktop mode** (local cache or PostgreSQL with sync) is optional in the spec and not implemented. The desktop requires the API.
* **Auto-update:** add `tauri-plugin-updater` with your signing key when the release channel is set up.
* **OpenSearch:** not needed at the measured scale. Re-evaluate if search latency exceeds targets.
* **Image copies:** exports default to image URLs. Copying images into storage is configurable per job (`image_handling`), but needs per-source licence confirmation before it is enabled.
