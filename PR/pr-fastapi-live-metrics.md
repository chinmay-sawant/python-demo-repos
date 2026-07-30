## Summary

Implement the full FastAPI live metrics ingest service for multi-tenant SaaS dashboards — batch ingest with idempotency, percentile read path, vendor fan-out, background TTL cleanup, and 11 passing tests.

---

## Motivation / context

- Plans: `fastapi-live-metrics-ingest/plans/`
- Issues: see **Related issues**

---

## Changes

### Project scaffolding & models

- FastAPI app with async lifespan managing SQLAlchemy engine/session lifecycle
- 5 SQLAlchemy async models: `Tenant`, `MetricSample`, `IngestBatch`, `WindowAggregate`, `VendorExportJob`
- Proper indexes on `(tenant_id, timestamp)` and `(tenant_id, route_label, timestamp)`
- Pydantic v2 schemas for request/response validation

### Ingest hot path

- `POST /api/v1/ingest` — batch accept metric samples with Pydantic validation (max 1000 per batch)
- Idempotency key support for safe agent retries
- Route label normalization with pre-compiled regex (module-level, not per-sample)
- User-agent classification into coarse buckets
- Bulk insert via `session.add_all()`
- Tenant authentication via `X-Tenant-Id` header

### Dashboard read path

- `GET /api/v1/tenants/{id}/percentiles` — p50/p95/p99 over a time window (Python-side computation)
- `GET /api/v1/tenants/{id}/top-routes` — top-N slowest routes via SQL GROUP BY
- `GET /api/v1/tenants/{id}/error-rates` — error count and rate via CASE aggregation
- All endpoints tenant-scoped with ISO datetime query params

### Vendor fan-out & background tasks

- `VendorExportService` with httpx async client, exponential backoff + jitter, timeout
- `BackgroundTaskManager` with TTL cleanup loop (batched deletion) and vendor export loop
- Shared httpx client created at startup, disposed on shutdown
- Semaphore-based concurrency limit on outbound calls

### Middleware

- `RequestTimingMiddleware` — monotonic timing, slow-request logging, `X-Request-Duration-Ms` header
- `TenantHeaderMiddleware` — parses `X-Tenant-Id` into request state

### Tests

- 11 tests covering health, ingest (success, empty, oversized, auth), percentiles, top-routes, error-rates, timing header, tenant middleware
- All use mocked dependencies — no real DB required

---

## Impact

| Area | Impact |
|------|--------|
| **Performance** | Bounded batch size, pre-compiled regex, bulk inserts, no blocking I/O on event loop |
| **Memory** | No unbounded buffers; samples streamed through validation into DB |
| **Behavior / correctness** | All-or-nothing batch, idempotent retries, best-effort vendor export |
| **API / CLI** | 4 new endpoints (ingest + 3 dashboard), background tasks |
| **Dependencies** | FastAPI, SQLAlchemy async, asyncpg, httpx, pydantic, alembic |
| **Binary size / build time** | N/A (Python) |

---

## Breaking changes / migration

| Item | Migration |
|------|-----------|
| None | Initial implementation — new project |

---

## Test plan

- [x] `python3 -m pytest tests/ -v` — 11/11 pass

### Commands

```sh
cd fastapi-live-metrics-ingest && python3 -m pytest tests/ -v
```

---

## Screenshots / sample output

```
tests/test_api.py::test_health_endpoint PASSED
tests/test_api.py::test_ingest_success PASSED
...
11 passed in 0.06s
```

---

## Related issues

- Relates to #1 (project init)

---

## PR metadata checklist (author)

- [x] Self-assigned (`--assignee @me`)
- [x] Labels applied
- [x] Related issues filled with real ticket IDs
- [x] Filled body committed under `PR/pr-fastapi-live-metrics.md`

---

## Follow-ups (out of scope)

- CodeHound detection rule fixtures (Phase 5)
- Real PostgreSQL migration instead of mocked DB
- Caching layer for dashboard reads
- Full auth product (stub API key only)
