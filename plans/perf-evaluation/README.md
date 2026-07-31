# Performance Evaluation — Index & Executive Summary

> **Parent:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/plans/phase-wise-checklist/SKILLS.md`
> **Status:** Evaluation complete (2026-07-31); improvements not started — per-phase ledgers below are live execution rows
> **Estimated effort:** Phase 1 ~2d · Phase 2 ~2d · Phase 3 ~1.5d · Phase 4 ~1d · Gates ~1d

---

## Overview

Performance evaluation of the three perf-sensitive Python services in this corpus, in README sequence:
**fastapi-live-metrics-ingest → django-flash-sale-inventory → flask-partner-webhook-relay**.
Every finding carries an ID, severity, impact, an **absolute path + line number**, and a code snippet of
the current code plus the proposed change.

## Baseline (evidence, 2026-07-31)

| Project | Test command | Result | Notes |
|---|---|---|---|
| fastapi-live-metrics-ingest | `python3 -m pytest -q` (in project dir) | 11 passed (0.07s) | Unit-only; no load benchmark exists |
| flask-partner-webhook-relay | `python3 -m pytest -q` (in project dir) | 7 passed, 1 xfailed (0.49s) | Unit-only; no load benchmark exists |
| django-flash-sale-inventory | `python3 manage.py test` (in project dir) | 24 passed, **1 FAILED** | `test_concurrent_reserve_no_deadlock` fails with sqlite `database table is locked` (`inventory/tests.py:399`) — the hot path already fails under concurrency on the default dev DB |

No `Makefile` exists in any project; the skill's `make lint` / `make test` gates map to
`python3 -m pytest` / `python3 manage.py test`. No benchmark harness, no locust/k6, no profiling
artifact exists anywhere in the tree.

## Top hotspots (highest to lowest impact)

| # | Project | Issue | Location | Impact |
|---|---|---|---|---|
| 1 | fastapi | Percentile read path pulls **all** rows to Python and sorts in memory | `01-fastapi-live-metrics-ingest.md#FA-1` | Read QPS collapses as table grows; O(N) transfer + O(N log N) sort per request |
| 1b | fastapi | Concurrent DB requests exhaust the engine pool (sessions not returned; 30s stalls → 500) — found by baseline bench | `01-fastapi-live-metrics-ingest.md#FA-9` | Read path → 0 req/s under ≥15 concurrent requests, any backend |
| 2 | fastapi | TTL cleanup full-table-scans `created_at` (no index) every 5 min | `01-fastapi-live-metrics-ingest.md#FA-3` | Writes stall; cleanup becomes O(table) per run |
| 3 | django | Reserve hot path: N+1 lookups + per-line INSERT x3 (3N+2 queries per order) | `02-django-flash-sale-inventory.md#DJ-1` / `#DJ-2` | Flash-sale checkout throughput capped by round-trips |
| 4 | django | confirm/cancel/expire do read-modify-write `.save()` on stock without `F()` → lost updates + extra round-trips | `02-django-flash-sale-inventory.md#DJ-3` | Data corruption risk under concurrency; doubles query count |
| 5 | flask | Delivery worker is **single-threaded sequential**; `DELIVERY_MAX_CONCURRENCY` config (`config.py:13`) is never used | `03-flask-partner-webhook-relay.md#FL-1` | One slow partner (30s timeout) blocks entire queue; fan-out latency = N×slowest |
| 6 | flask | `claim_work` has no `FOR UPDATE SKIP LOCKED` → concurrent workers double-deliver | `03-flask-partner-webhook-relay.md#FL-2` | At-least-once violated; duplicates to partners |

## File map

| File | Contents |
|---|---|
| [`01-fastapi-live-metrics-ingest.md`](./01-fastapi-live-metrics-ingest.md) | Phase 1 — FA-1..FA-8: read path, indexes, TTL cleanup, middleware, pool, regex, idempotency, vendor export |
| [`02-django-flash-sale-inventory.md`](./02-django-flash-sale-inventory.md) | Phase 2 — DJ-1..DJ-8: reserve hot path, F() atomicity, expiry sweep, idempotency race, availability reads, Postgres, index |
| [`03-flask-partner-webhook-relay.md`](./03-flask-partner-webhook-relay.md) | Phase 3 — FL-1..FL-8: worker concurrency, atomic claim, N+1, payload handling, purge, polling, Postgres, DLQ redrive |
| [`04-cross-cutting-and-gates.md`](./04-cross-cutting-and-gates.md) | Phase 4 — XC-1..XC-5 (bench harness, pooling, server config, observability, semgrep) + Phase 5 closure gates + dependencies |

## Ledger rules (from parent skill)

- `[ ]` not started or not proven; `[x]` implemented and validated with current evidence; `[~]` deferred/partial with reason + next gate.
- A row closes only when the matching source/test/benchmark check succeeds; performance rows additionally require the load-benchmark proof in `04-cross-cutting-and-gates.md` (Phase 5) — successful test execution is **not** benchmark proof.
- Update a row, record the exact command and outcome inline; keep release measurements distinct from dev-loop measurements.

## Dependencies (cross-phase, detailed in 04)

- Phase 1: Postgres fixture for EXPLAIN work; do FA-2 (index) before FA-1 validation.
- Phase 2: DJ-7 (Postgres) **before** DJ-3/DJ-4 validation (row locks are the premise); DJ-1 before DJ-2.
- Phase 3: FL-2 (atomic claim) **before** FL-1 (concurrency) — threads without atomic claim double-deliver; FL-7 before load validation.
- Phase 4: XC-1 is the measuring stick for all closure gates; XC-5 is independent security hardening.
