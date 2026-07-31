# codehound-python-perf-targets

Performance-sensitive **Python service requirements** used to shape CodeHound’s
Python PERF / framework-footgun catalog. This tree is **plans only** — no
application source, no snippets.

> Entire project authored by **DeepSeek V4 Flash**. Every requirement, plan, and
> acceptance criterion was generated with DeepSeek V4 Flash.

Parent path:

```text
/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets
```

## Project folders

| Folder | Stack focus | Use case |
|--------|-------------|----------|
| [`fastapi-live-metrics-ingest`](./fastapi-live-metrics-ingest/) | FastAPI, asyncio, SQLAlchemy async, httpx/aiohttp | Live SaaS metrics ingest + percentile read path |
| [`django-flash-sale-inventory`](./django-flash-sale-inventory/) | Django, Django ORM, PostgreSQL | Flash-sale multi-warehouse inventory reservation |
| [`flask-partner-webhook-relay`](./flask-partner-webhook-relay/) | Flask, sync workers, requests, relational store | B2B partner webhook fan-out with retries |

Database is **not** a separate project. Persistence requirements live inside each
framework plan (connection pooling, query-in-loop, transaction boundaries,
batch writes).

## Why these use cases (not generic CRUD)

Each domain forces **hot-path** work under load: high ingest QPS, concurrent
stock reservation, or outbound HTTP fan-out. Requirements deliberately surface
patterns CodeHound should detect later (regex thrash, string building in loops,
missing client timeouts, ORM N+1, blocking I/O in async routes, resource leaks).

## How to read a project

1. Project `README.md` — domain, SLOs, non-goals, tech boundary.
2. `plans/` — phase-wise **checklist** plans (requirements + acceptance only).

## Perf findings & measured improvements (2026-08-01)

Perf findings were detected with **goslop** (perf/security catalogue, exports in
`scripts/findings/`), confirmed with k6 + in-process benchmarks (see
[`benchmarks.md`](./benchmarks.md)), then fixed per the execution ledgers in
[`plans/perf-evaluation/`](./plans/perf-evaluation/). Both fixes below are
shipped and verified; the fastapi repo is skipped (see note at the end).

### django-flash-sale-inventory — findings → fixes

| # | Finding | Fix |
|---|---------|-----|
| DJ-1 | N+1 Sku/Warehouse lookups per item line | Hoisted lookups out of the item loop |
| DJ-2 | 3N per-row writes (create/save in loop) | `bulk_create` + `F()` expressions |
| DJ-3 | read-modify-write `.save()` → lost updates under concurrency | `F()`-based atomic updates |
| DJ-4 | `release_expired` one unbounded transaction | Chunked processing (500/batch) |
| DJ-5 | idempotency race → IntegrityError → 500 | Catch + return existing reservation |
| DJ-6 | Unbounded request body + ORM hydration on read path | Size cap + `values()` reads |
| DJ-7 | sqlite default + `DEBUG=True` | Env-driven Postgres default, `CONN_MAX_AGE`, `DEBUG` gated |
| DJ-8 | `-quantity` sort with no supporting index | `(sku, -quantity)` index + migration |

**Measured:** reserve(20 lines) **102 queries/34ms → 45 queries/17ms**;
confirm(20 lines) **166q/68.4ms → 72q/32.5ms**; batch availability **123 → ~137
req/s**; rollup flat at ~175 req/s; tests **24/25 → 31/31 pass**.

### flask-partner-webhook-relay — findings → fixes

| # | Finding | Fix |
|---|---------|-----|
| FL-1 | Sequential delivery; `DELIVERY_MAX_CONCURRENCY` unused | `ThreadPoolExecutor` fan-out + per-endpoint cap |
| FL-2 | Non-atomic claim → double delivery | `FOR UPDATE SKIP LOCKED` claim |
| FL-3 | N+1 lazy loads per outbox row | `joinedload` on claim + deliver |
| FL-4 | Payload parse → dump → store (lossy, double parse) | Raw-byte storage, single parse |
| FL-5 | Purge: full scan + giant transaction | Indexes + chunked delete |
| FL-6 | Poll loop always sleeps, even after work | Continue immediately when work delivered |
| FL-7 | sqlite default, no pooling | Env-driven Postgres default + pool options |
| FL-8 | DLQ redrive loads whole table | Set-based bulk update |

**Measured:** 50-item fan-out to a 200ms partner **10.40s → 1.05s (9.9×)**;
ingest **116 → 121–146 req/s**; purge 100k rows **4.8s** (bounded memory);
DLQ redrive 100k rows **0.46s, flat RSS**; tests **7+1 xfail → 24 pass + 1 skip
+ 1 xfail** (skip = FL-2 proof, Postgres-gated).

### fastapi-live-metrics-ingest — skipped

Findings are implemented (FA-1..FA-9, tests 43 pass) but the headline win is
**Postgres-gated**: percentiles pushdown (`percentile_cont`, FA-1) only pays on
Postgres, and the concurrent read-path issue (FA-9) remains open on the sqlite
dev stack — a sqlite-level large-fetch concurrency problem, not the middleware
interaction originally suspected. Re-validate once a Postgres host is available.

## Non-goals for this corpus

- Shipping runnable apps in this tree (plans first).
- Full security SAST coverage (PERF + framework footguns first).
- Replacing ruff / bandit / mypy.
- A standalone “database” or “SQLAlchemy-only” project folder.
