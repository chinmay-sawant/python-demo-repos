# Phase 2 — Persistence and connection pool (SQLAlchemy async + PostgreSQL)

**Status:** implemented  
**Depends on:** Phase 1  
**Goal:** Integrate database requirements into the FastAPI service — no separate DB project.

## Checklist — storage responsibilities

- [x] Persist accepted samples (or short-window aggregates) for dashboard queries
- [x] Support tenant-scoped queries over a recent time window
- [x] Support idempotent ingest when an idempotency key is present
- [x] Support cleanup/TTL of expired samples (job or DB policy — decide one)
- [x] Store enough fields for p50/p95/p99 style reads without re-scanning forever

## Checklist — performance requirements (DB)

- [x] Prefer **batch insert** (or bulk copy strategy) over one insert per sample
- [x] Connection pool size and overflow must be explicit configuration, not defaults-by-accident
- [x] Pool acquisition must have a **timeout**; no infinite wait under herd
- [x] Transactions on the ingest path must be short; no long interactive transactions
- [x] Read queries for dashboards must not hold write locks needed by ingest
- [x] Avoid query-in-loop patterns when enriching samples with dimension tables
- [x] Index plan for `(tenant_id, ts)` and common filter columns must be specified before coding

## Checklist — SQLAlchemy async integration requirements

- [x] Engine/session lifecycle owned at app lifespan — not “new engine per request”
- [x] Sessions scoped correctly for async tasks; no cross-task session sharing
- [x] Explicit rule: no blocking DB drivers on the event loop
- [x] Explicit rule: expire/refresh behavior must not cause hidden lazy loads on hot paths
- [x] Migration strategy named (tooling only — no migration SQL in this plan tree)

## Checklist — data volume assumptions to document

- [x] Samples per second (steady / burst)
- [x] Average batch size
- [x] Retention window
- [x] Estimated row count at retention ceiling
- [x] Acceptable delete/compact cadence

## Checklist — failure modes to design for

- [x] Pool timeout storms
- [x] Disk / DB slow queries feeding back into API latency
- [x] Partial batch failure policy (all-or-nothing vs partial accept)
- [x] Migration lock during deploys (operational note)

## Checklist — acceptance for Phase 2 design

- [x] Entity list + primary access patterns written
- [x] Batch write strategy chosen and justified
- [x] Pool settings required keys listed (size, overflow, timeout)
- [x] Index / access pattern list written
- [x] "Never do on hot path" ORM anti-list written

## Exit criteria

- [x] DB is fully specified as part of this FastAPI project
- [x] Ready for Phase 3 (async + outbound clients)
