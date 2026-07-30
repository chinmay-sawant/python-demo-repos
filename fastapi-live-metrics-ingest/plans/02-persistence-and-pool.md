# Phase 2 — Persistence and connection pool (SQLAlchemy async + PostgreSQL)

**Status:** planning  
**Depends on:** Phase 1  
**Goal:** Integrate database requirements into the FastAPI service — no separate DB project.

## Checklist — storage responsibilities

- [ ] Persist accepted samples (or short-window aggregates) for dashboard queries
- [ ] Support tenant-scoped queries over a recent time window
- [ ] Support idempotent ingest when an idempotency key is present
- [ ] Support cleanup/TTL of expired samples (job or DB policy — decide one)
- [ ] Store enough fields for p50/p95/p99 style reads without re-scanning forever

## Checklist — performance requirements (DB)

- [ ] Prefer **batch insert** (or bulk copy strategy) over one insert per sample
- [ ] Connection pool size and overflow must be explicit configuration, not defaults-by-accident
- [ ] Pool acquisition must have a **timeout**; no infinite wait under herd
- [ ] Transactions on the ingest path must be short; no long interactive transactions
- [ ] Read queries for dashboards must not hold write locks needed by ingest
- [ ] Avoid query-in-loop patterns when enriching samples with dimension tables
- [ ] Index plan for `(tenant_id, ts)` and common filter columns must be specified before coding

## Checklist — SQLAlchemy async integration requirements

- [ ] Engine/session lifecycle owned at app lifespan — not “new engine per request”
- [ ] Sessions scoped correctly for async tasks; no cross-task session sharing
- [ ] Explicit rule: no blocking DB drivers on the event loop
- [ ] Explicit rule: expire/refresh behavior must not cause hidden lazy loads on hot paths
- [ ] Migration strategy named (tooling only — no migration SQL in this plan tree)

## Checklist — data volume assumptions to document

- [ ] Samples per second (steady / burst)
- [ ] Average batch size
- [ ] Retention window
- [ ] Estimated row count at retention ceiling
- [ ] Acceptable delete/compact cadence

## Checklist — failure modes to design for

- [ ] Pool timeout storms
- [ ] Disk / DB slow queries feeding back into API latency
- [ ] Partial batch failure policy (all-or-nothing vs partial accept)
- [ ] Migration lock during deploys (operational note)

## Checklist — acceptance for Phase 2 design

- [ ] Entity list + primary access patterns written
- [ ] Batch write strategy chosen and justified
- [ ] Pool settings required keys listed (size, overflow, timeout)
- [ ] Index / access pattern list written
- [ ] “Never do on hot path” ORM anti-list written

## Exit criteria

- [ ] DB is fully specified as part of this FastAPI project
- [ ] Ready for Phase 3 (async + outbound clients)
