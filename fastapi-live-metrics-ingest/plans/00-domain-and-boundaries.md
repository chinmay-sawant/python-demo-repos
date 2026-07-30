# Phase 0 — Domain and boundaries

**Status:** implemented  
**Project:** `fastapi-live-metrics-ingest`  
**Goal:** Lock the use case so later phases specify performance requirements, not generic features.

## Checklist — domain lock

- [x] Confirm product name in docs: **Live metrics ingest for multi-tenant SaaS dashboards**
- [x] Confirm write path: agents POST **batches** of samples (not one HTTP call per sample only)
- [x] Confirm read path: dashboard needs **p50/p95/p99 latency** and **top slow routes** over a short window
- [x] Confirm side path: optional **vendor fan-out** of rolled-up counters (not raw firehose)
- [x] Confirm tenancy: every sample and query is scoped by `tenant_id`
- [x] Confirm retention: short window (e.g. hours), not long-term warehouse

## Checklist — actors

- [x] Browser / edge **agent** (high volume, untrusted shape of labels)
- [x] **Dashboard** user (lower volume, latency-sensitive reads)
- [x] **Operator** (health, drop rates, pool saturation)
- [x] **Third-party analytics vendor** (outbound HTTPS, flaky)

## Checklist — load shape (requirements, not implementation)

- [x] Define expected **burst multiple** vs steady-state (launch spikes)
 - [x] Define max **batch size** accepted per request
 - [x] Define max **concurrent** ingest connections planning assumption
 - [x] Define max **cardinality** of route labels before aggregation/drop
 - [x] Define what "degraded mode" means (drop samples vs slow the client)

## Checklist — data concepts (names only)

- [x] `Tenant`
- [x] `MetricSample` (timestamp, route label, latency_ms, status class, ua_class)
- [x] `IngestBatch` (idempotency key optional)
- [x] `WindowAggregate` (pre-rolled or query-time — decide in Phase 2/4)
- [x] `VendorExportJob` (async side path)

## Checklist — non-goals (must stay out of scope)

- [x] No full auth product (stub API key / JWT verify is enough for load paths)
- [x] No multi-region active-active
- [x] No ClickHouse/Druid replacement in phase plans
- [x] No UI implementation requirements beyond API contracts
- [x] No code snippets in any plan file

## Exit criteria

- [x] One-paragraph problem statement agreed
- [x] Load shape bullets agreed
- [x] Non-goals agreed
- [x] Ready for Phase 1 (ingest hot path requirements)
