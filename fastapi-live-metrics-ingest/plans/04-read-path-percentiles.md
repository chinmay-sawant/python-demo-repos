# Phase 4 — Read path (percentiles and top routes)

**Status:** implemented  
**Depends on:** Phase 2 (data), Phase 3 (must not steal the loop)  
**Goal:** Specify dashboard query requirements that stay cheap under concurrent ingest.

## Checklist — functional requirements

- [x] Query p50/p95/p99 latency for a tenant over a time window
- [x] Query top-N slowest route labels in the window
- [x] Query error-rate by status class
- [x] All queries tenant-scoped
- [x] Support short windows suitable for “live” dashboards (e.g. last 1–15 minutes)

## Checklist — performance requirements

- [x] Read path must not table-scan unbounded history
- [x] Prefer pre-aggregation or constrained raw scan — **choose and document**
- [x] Concurrent dashboard users must not starve ingest pool (pool partitioning or statement timeouts)
- [x] Response payload size bounded (top-N caps)
- [x] No per-row Python-side percentile over huge lists without a plan (DB or sketch)
- [x] Caching (if used) has TTL and invalidation rules; not a silent second source of truth forever

## Checklist — correctness under load

- [x] Define freshness: how stale aggregates may be
- [x] Define behavior when data is partial during ingest lag
- [x] Define empty-window response shape

## Checklist — anti-patterns to avoid on read path

- [x] Loading all samples into app memory then sorting in Python for large windows
- [x] N+1 dimension lookups per route label
- [x] Recomputing heavy regex classification at read time if already stored as class

## Checklist — acceptance for Phase 4 design

- [x] API query parameters and limits written
- [x] Aggregation strategy chosen
- [x] Statement timeout / pool fairness notes written
- [x] Freshness SLA sentence written

## Exit criteria

- [x] Read-path checklist complete
- [x] Ready for Phase 5 (CodeHound detection targets)
