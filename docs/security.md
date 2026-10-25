# Security checklist

| Area | Implementation | Where |
|---|---|---|
| Passwords | Argon2id, rehashed on login when parameters change; minimum 12 characters | `core/security.py` |
| User enumeration | Identical error and similar timing for unknown email and wrong password (dummy hash) | `api/v1/auth.py` |
| Brute force | Per-IP and per-email sliding-window limit (Redis, in-memory fallback) | `api/deps.py` |
| Sessions | 15-minute HS256 access JWT; opaque 30-day refresh tokens stored as SHA-256; rotated on every use; **reuse revokes the whole token family**; revoked on role change or deactivation | `api/v1/auth.py` |
| Token storage | Web: `httpOnly`, `SameSite=Strict` cookie scoped to `/api/v1/auth`, access token kept in memory. Desktop: OS credential store (Windows Credential Manager) | `web/lib/api.ts`, `desktop/src-tauri/src/lib.rs` |
| Tenant isolation | Every query filters by `organisation_id`; listings are tenant-scoped; tests check that cross-org access returns 404 | all routers, `tests/test_api_auth.py` |
| RBAC | Permission matrix checked on every request; own-account data needs `view_own_account_data` | `core/security.py`, `api/deps.py` |
| Own vs competitor data | Explicit `source_type`; own-account rows are never downgraded; hidden from roles without permission; exports drop them unless the user holds `export_own_account_data` | `services/ingest.py`, `services/products.py` |
| Connector credentials | Encrypted secrets store (Fernet locally; plug in a cloud secrets manager); SQL stores only `credential_reference`; deleted on disconnect; refreshed tokens are written back to the store, not SQL | `core/secrets.py`, `services/sync.py` |
| OAuth | Single-use, 15-minute `state` bound to user and account; minimal scopes | `api/v1/connections.py` |
| SSRF | User URLs are only *parsed*, against each connector's host allow-list; any fetch resolves DNS and rejects private, loopback, link-local, reserved and IPv4-mapped addresses; credentials-in-URL and non-HTTP schemes are rejected; redirects are not followed | `core/ssrf.py` |
| Desktop download | Accepts only URLs on the configured API origin; filenames are sanitised; files are saved to the Downloads folder | `desktop/src-tauri/src/lib.rs` |
| Exports | Background generation; private storage; 15-minute user-bound HMAC download links; formula-injection neutralisation (`= + - @` tab, CR and full-width variants); manifest; audit log entries; automatic expiry | `services/exports.py` |
| Cursors | HMAC-signed and tamper-evident | `core/pagination.py` |
| Logging | Request logs contain path and status only (no query strings, which may carry tokens); audit details are scrubbed of password, token and secret keys | `main.py`, `services/audit.py` |
| Headers | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, HSTS when secure cookies are enabled; strict CSP in the desktop app | `main.py`, `tauri.conf.json` |
| Marketplace terms | Official APIs only. No CAPTCHA, login or paywall circumvention. No scraping. Planned connectors return no data until approved | `connectors/` |

## Before production

- [ ] Set strong `MPI_JWT_SECRET`, `MPI_CURSOR_SECRET` and `MPI_SECRETS_ENCRYPTION_KEY`; set `MPI_ENV=production` and `MPI_SECURE_COOKIES=true`
- [ ] Replace `LocalSecretStore` with the cloud secrets manager implementation
- [ ] Use a private network for PostgreSQL and Redis; expose only the API and web host
- [ ] Set `MPI_ALLOW_SELF_SIGNUP=false` if organisations are provisioned by an administrator
- [ ] Code-sign the Windows installer (`certificateThumbprint`) and publish through the controlled release process
- [ ] Run a dependency and container scan, plus an external penetration test
- [ ] Review the data retention periods for exports, observations and audit logs against your licences and privacy obligations
