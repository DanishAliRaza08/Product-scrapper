# API summary

Base path `/api/v1`. The full OpenAPI document is at `/api/v1/openapi.json` (interactive at `/api/v1/docs`).

Conventions:

* JSON bodies validated with Pydantic. Errors always use
  `{"error": {"code", "message", "details"}}` with codes such as `validation_error`,
  `unauthorized`, `forbidden`, `not_found`, `conflict`, `rate_limited`,
  `unsupported_input`, `connector_not_configured` and `invalid_cursor`.
* Money is returned as decimal **strings** (`"24.99"`) together with its currency.
* Large collections use opaque, HMAC-signed `cursor` / `next_cursor` keyset pagination.
  The API never returns unbounded result sets.
* Every resource query is scoped to the caller's organisation. Role permissions are
  checked on every request.
* `POST /jobs` accepts an `Idempotency-Key` header, so retries never create duplicate jobs.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me` |
| Users | `GET/POST /users`, `PATCH /users/{id}` |
| Reference | `GET /marketplaces`, `GET /connectors` (capability manifests + health) |
| Projects | `GET/POST /projects`, `PATCH /projects/{id}`, `POST /projects/{id}/archive`, `POST /projects/{id}/products`, `POST /projects/{id}/products/remove`, `GET/POST /projects/{id}/shops`, `GET/POST/DELETE /saved-searches` |
| Connections | `GET/POST /connections`, `GET /connections/callback/{platform}`, `POST /connections/{id}/sync`, `DELETE /connections/{id}` |
| Jobs | `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/items`, `GET /jobs/{id}/events`, `GET /jobs/{id}/stream` (SSE), `POST /jobs/{id}/cancel`, `POST /jobs/{id}/resume` |
| Products | `GET /products` (filters, sort, cursor), `GET/PATCH /products/{id}`, `GET /products/{id}/history`, `/metrics`, `/sources`, `GET/POST /products/{id}/costs`, `GET /products/{id}/profit`, `GET /products/{id}/opportunity`, `POST /products/compare` |
| Matching | `POST /products/{id}/matches/suggest`, `GET /matches`, `POST /matches/{id}/review` |
| Analytics | `GET /analytics/overview`, `GET /analytics/own-account`, `GET /analytics/profit`, `POST /analytics/profit/calculate`, `GET /analytics/opportunities` |
| Costs and FX | `GET/POST/DELETE /fee-profiles`, `GET/POST /exchange-rates`, `GET /exchange-rates/convert` |
| Exports | `POST /exports`, `GET /exports`, `GET /exports/{id}`, `POST /exports/{id}/link`, `GET /exports/{id}/download?u=&token=` |
| Alerts | `GET /notifications`, `POST /notifications/read-all`, `GET/POST/DELETE /alert-rules` |
| Audit | `GET /audit-logs` |
| Health | `GET /health`, `GET /ready` |

## Roles

| Permission | admin | research_manager | researcher | viewer |
|---|---|---|---|---|
| view | ✓ | ✓ | ✓ | ✓ |
| run_jobs, export, edit_costs, manage_projects | ✓ | ✓ | ✓ | |
| review_matches, view_own_account_data | ✓ | ✓ | | |
| manage_connections, export_own_account_data, manage_users, view_audit | ✓ | | | |
