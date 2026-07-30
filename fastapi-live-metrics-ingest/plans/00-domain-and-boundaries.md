# Phase 0 — Domain and boundaries

**Status:** planning  
**Project:** `fastapi-live-metrics-ingest`  
**Goal:** Lock the use case so later phases specify performance requirements, not generic features.

## Checklist — domain lock

- [ ] Confirm product name in docs: **Live metrics ingest for multi-tenant SaaS dashboards**
- [ ] Confirm write path: agents POST **batches** of samples (not one HTTP call per sample only)
- [ ] Confirm read path: dashboard needs **p50/p95/p99 latency** and **top slow routes** over a short window
- [ ] Confirm side path: optional **vendor fan-out** of rolled-up counters (not raw firehose)
- [ ] Confirm tenancy: every sample and query is scoped by `tenant_id`
- [ ] Confirm retention: short window (e.g. hours), not long-term warehouse

## Checklist — actors

- [ ] Browser / edge **agent** (high volume, untrusted shape of labels)
- [ ] **Dashboard** user (lower volume, latency-sensitive reads)
- [ ] **Operator** (health, drop rates, pool saturation)
- [ ] **Third-party analytics vendor** (outbound HTTPS, flaky)

## Checklist — load shape (requirements, not implementation)

- [ ] Define expected **burst multiple** vs steady-state (launch spikes)
- [ ] Define max **batch size** accepted per request
- [ ] Define max **concurrent** ingest connections planning assumption
- [ ] Define max **cardinality** of route labels before aggregation/drop
- [ ] Define what “degraded mode” means (drop samples vs slow the client)

## Checklist — data concepts (names only)

- [ ] `Tenant`
- [ ] `MetricSample` (timestamp, route label, latency_ms, status class, ua_class)
- [ ] `IngestBatch` (idempotency key optional)
- [ ] `WindowAggregate` (pre-rolled or query-time — decide in Phase 2/4)
- [ ] `VendorExportJob` (async side path)

## Checklist — non-goals (must stay out of scope)

- [ ] No full auth product (stub API key / JWT verify is enough for load paths)
- [ ] No multi-region active-active
- [ ] No ClickHouse/Druid replacement in phase plans
- [ ] No UI implementation requirements beyond API contracts
- [ ] No code snippets in any plan file

## Exit criteria

- [ ] One-paragraph problem statement agreed
- [ ] Load shape bullets agreed
- [ ] Non-goals agreed
- [ ] Ready for Phase 1 (ingest hot path requirements)
