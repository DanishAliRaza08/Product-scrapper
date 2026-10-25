# Operations

## Recommended deployment

| Component | Recommendation |
|---|---|
| PostgreSQL | Managed service (RDS, Cloud SQL or Azure Flexible Server) with PITR and automated backups; PgBouncer in transaction mode once API and worker connections grow |
| Redis | Managed Redis; used for broker, rate limiting and short-lived cache only |
| API | Containers behind HTTPS load balancer; `uvicorn --proxy-headers`; scale on CPU and latency |
| Workers | Separate deployment from the API; `jobs` and `exports` queues scale independently; `--concurrency` × connector `max_concurrency` bounds load on each source |
| Beat | Exactly **one** replica |
| Object storage | Private S3-compatible bucket (`MPI_STORAGE_BACKEND=s3`), versioning on, lifecycle rule for `exports/` |
| Web | Static hosting or CDN for `web/out` |
| Environments | Separate dev, staging and production, each with its own secrets; infrastructure as code |

Run `alembic upgrade head` as a one-off release step before new API or worker versions start.

## Scheduled tasks (Celery beat)

| Task | Schedule | Purpose |
|---|---|---|
| `recover_stale_jobs_task` | 5 min | Requeue jobs whose worker heartbeat stopped |
| `schedule_refreshes_task` | hourly | Refresh watched, volatile and stale listings, one refresh job per org and marketplace (respects quotas) |
| `refresh_summary_view_task` | 10 min | `REFRESH MATERIALIZED VIEW CONCURRENTLY mv_listing_summary` |
| `ensure_partitions_task` | daily | Create monthly observation partitions ahead of time |
| `expire_exports_task` | daily | Delete expired export files |
| `refresh_fx_task` | daily | Import ECB reference rates |
| `connector_health_task` | 10 min | Record connector status |

## Monitoring

Track and alert on:

* **Jobs:** queued and running counts, failure and partial rates, products processed per connector, paused jobs (access problems)
* **Connectors:** 429 rate, 5xx rate, circuit-breaker opens, `connector_health.consecutive_failures`
* **API:** p95 latency, 5xx rate, 401/403 spikes (unusual access)
* **Workers:** queue depth (`redis-cli llen jobs`), utilisation, task runtime
* **Database:** storage growth, slow queries (`pg_stat_statements`), connection pool use, replication and backup status, presence of next month's observation partition
* **Exports:** failures; own-account exports (`audit_logs.action = 'export.link_issued'`)
* **Accounts:** stale syncs (`sync_checkpoints.last_successful_sync_at`), expired authorisations (`marketplace_accounts.connection_status`)

## Backup and recovery

* Automated daily snapshots plus point-in-time recovery (WAL), with retention set by policy.
* **Monthly restore drill** into an isolated environment. Verify the row counts of
  `marketplace_listings`, `listing_observations` and `seller_orders`; confirm `alembic current`
  matches; run `pytest` against a restored copy of the schema.
* Object storage versioning protects export manifests and permitted image copies.
* Suggested objectives: RPO ≤ 15 minutes (PITR) and RTO ≤ 4 hours. Agree final values with the business.
* Secrets store backups are encrypted and kept separately from database backups.

## Data retention

* Exports expire after `MPI_EXPORT_RETENTION_DAYS` (default 7).
* Observation partitions older than the licensed retention period can be detached and archived:
  `ALTER TABLE listing_observations DETACH PARTITION listing_observations_2026_01;`
* Deleting an organisation cascades to all of its tenant data. Observations are removed by
  the retention job, because the partitioned table deliberately has no cascading foreign key.
