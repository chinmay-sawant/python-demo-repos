# fastapi-live-metrics-ingest

**Stack:** FastAPI · asyncio · SQLAlchemy (async) · PostgreSQL · httpx / aiohttp  
**Plans only:** see [`plans/`](./plans/)

## Domain (specific, performance-sensitive)

Build a **live product-metrics ingest API** for a multi-tenant SaaS dashboard.

Product teams ship browser and edge agents that stream:

- page-view samples
- API latency histograms (client-side)
- error-rate counters
- coarse user-agent / geo labels

The service must:

1. Accept **high-QPS batch ingest** of metric samples (write path).
2. Persist samples for short-retention analytics (minutes–hours).
3. Serve **near-real-time percentile and top-path** queries for the dashboard
   (read path).
4. Optionally **fan out** selected aggregates to a third-party analytics vendor
   over HTTPS (outbound client path).

This is **not** a generic employee CRUD app. The hard problem is surviving
ingest spikes during product launches without melting the event loop, the DB
pool, or the outbound HTTP layer.

## Primary performance SLOs (planning targets)

| Path | Target direction |
|------|------------------|
| Ingest | Sustain burst traffic with bounded p99 latency; no unbounded memory growth |
| Read (percentiles) | Dashboard refresh feels “live” (sub-second typical; degrade gracefully under load) |
| Outbound fan-out | Must not block request handlers; must use timeouts and connection reuse |
| DB | Pool exhaustion and N+1-style per-sample inserts are first-class failure modes |

## Technologies in scope (integrated, not separate folders)

- FastAPI / Starlette request lifecycle
- asyncio event-loop rules (no blocking I/O on the loop)
- SQLAlchemy async engine + PostgreSQL (writes, batching, read aggregates)
- httpx and/or aiohttp shared clients for vendor fan-out
- Hot-path string/JSON/regex work on user-agent and path labels

## Explicit non-goals

- Perfect multi-region consistency
- Full OLAP warehouse (ClickHouse-class) in v1 of the plan
- Security-grade taint / authz product (auth exists only as a load-bearing path)
- Code samples in these plans

## Plan index

| Phase | File | Theme |
|-------|------|--------|
| 0 | [`plans/00-domain-and-boundaries.md`](./plans/00-domain-and-boundaries.md) | Domain lock, actors, non-goals |
| 1 | [`plans/01-ingest-hot-path.md`](./plans/01-ingest-hot-path.md) | Batch ingest, validation, parsing thrash |
| 2 | [`plans/02-persistence-and-pool.md`](./plans/02-persistence-and-pool.md) | SQLAlchemy async, pooling, batch writes |
| 3 | [`plans/03-async-and-outbound-clients.md`](./plans/03-async-and-outbound-clients.md) | asyncio rules, httpx/aiohttp lifecycle |
| 4 | [`plans/04-read-path-percentiles.md`](./plans/04-read-path-percentiles.md) | Dashboard query path under load |
| 5 | [`plans/05-codehound-detection-targets.md`](./plans/05-codehound-detection-targets.md) | What CodeHound should eventually flag |
| 6 | [`plans/06-acceptance-and-pilot.md`](./plans/06-acceptance-and-pilot.md) | Pilot checklist before implementation |
