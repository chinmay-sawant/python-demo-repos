# Phase 4 — Read path (percentiles and top routes)

**Status:** planning  
**Depends on:** Phase 2 (data), Phase 3 (must not steal the loop)  
**Goal:** Specify dashboard query requirements that stay cheap under concurrent ingest.

## Checklist — functional requirements

- [ ] Query p50/p95/p99 latency for a tenant over a time window
- [ ] Query top-N slowest route labels in the window
- [ ] Query error-rate by status class
- [ ] All queries tenant-scoped
- [ ] Support short windows suitable for “live” dashboards (e.g. last 1–15 minutes)

## Checklist — performance requirements

- [ ] Read path must not table-scan unbounded history
- [ ] Prefer pre-aggregation or constrained raw scan — **choose and document**
- [ ] Concurrent dashboard users must not starve ingest pool (pool partitioning or statement timeouts)
- [ ] Response payload size bounded (top-N caps)
- [ ] No per-row Python-side percentile over huge lists without a plan (DB or sketch)
- [ ] Caching (if used) has TTL and invalidation rules; not a silent second source of truth forever

## Checklist — correctness under load

- [ ] Define freshness: how stale aggregates may be
- [ ] Define behavior when data is partial during ingest lag
- [ ] Define empty-window response shape

## Checklist — anti-patterns to avoid on read path

- [ ] Loading all samples into app memory then sorting in Python for large windows
- [ ] N+1 dimension lookups per route label
- [ ] Recomputing heavy regex classification at read time if already stored as class

## Checklist — acceptance for Phase 4 design

- [ ] API query parameters and limits written
- [ ] Aggregation strategy chosen
- [ ] Statement timeout / pool fairness notes written
- [ ] Freshness SLA sentence written

## Exit criteria

- [ ] Read-path checklist complete
- [ ] Ready for Phase 5 (CodeHound detection targets)
