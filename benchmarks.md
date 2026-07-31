# Baseline Benchmarks — 2026-07-31

> **Canonical ledger:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/plans/perf-evaluation/README.md`
> **Parent skill:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/plans/phase-wise-checklist/SKILLS.md`
> **Status:** Baselines recorded 2026-07-31 (the `before` side); **post-fix measurements appended 2026-08-01** below each section. Re-run per Phase 5 gates in `04-cross-cutting-and-gates.md`.

## Environment (must be identical for re-runs)

| Item | Value |
|---|---|
| Host | i7-13700HX, 24 logical cores, 7.6 GiB RAM, Linux |
| Python | 3.13.10 |
| k6 | v1.4.2 (go1.25.4) |
| SQLAlchemy | 2.x, asyncpg/psycopg2 clients installed |
| **Postgres server** | **NOT available** — every benchmark below ran on **SQLite** (file DB), which is the repo default |
| HTTP servers | uvicorn 0.52 (fastapi, 1 worker), Django `runserver` (dev server), Flask/Werkzeug dev server (threaded) |
| Load pattern | k6 constant-arrival-rate, 20s runs, warm cache unless stated; results from `--summary-export` |

**Caveat:** these are dev-stack baselines (sqlite + dev servers), not the Phase-5 production gates
(Postgres + prod server). They are the honest "as-shipped" numbers and the `before` side of every ledger row.

---

## 1. fastapi-live-metrics-ingest

Harness: `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/bench/`
(`seed_db.py` seeds 200k rows; `ingest.js`, `percentiles.js`, `microbench.py`).

### 1a. `POST /api/v1/ingest` (100 samples/batch) — target 300/s, 20s

| Metric | Value |
|---|---|
| Achieved throughput | **29.2 req/s** (676 reqs; 5325 dropped iterations) |
| Latency | avg 3.15s · **med 2.94s** · p90 4.41s · p95 5.25s · max 8.01s |
| Errors | 0.29% (2/676) |

Command: `METRICS_DATABASE_URL=sqlite+aiosqlite:///bench_metrics.db python3 -m uvicorn app.main:app --port 8101` then `k6 run bench/ingest.js`.
Dataset: 200k seeded rows + ~68k rows written during the run. Latency inflated by sqlite write serialization **and** pool exhaustion (see 1c).

### 1b. `GET /api/v1/tenants/1/percentiles` (267,401 rows in window)

| Condition | Result |
|---|---|
| Single request, warm cache, fresh pool | **0.56–0.67s** (3 samples) |
| 20 concurrent VUs, 20s | **0 req/s** — every request stalls exactly 30s then 500 (`pool_timeout`) |

Command: `k6 run bench/percentiles.js` (20 VUs). The 0.6s single-request cost is the FA-1 O(N) transfer + app-side sort
(app-side sort alone measures 12.6ms @100k rows / 216.9ms @1M rows — `bench/microbench.py`).

### 1c. NEW FINDING — FA-9 [HIGH]: concurrent DB-backed requests exhaust the async engine pool

Repro: fresh uvicorn → 8 concurrent GETs to `/percentiles` via async httpx (6s client timeout) → **all 8 ReadTimeout**;
a subsequent single request **hangs >40s** (pool dead). `/health` under 20 VUs: **493 req/s, med 40ms** — HTTP stack fine.
Raw SQLAlchemy sessions (bypassing HTTP): 20 concurrent SELECTs complete in **1.9s** — DB fine.

**Diagnosis:** sessions/connections checked out through the FastAPI dependency (`app/dependencies.py:9-12`) are not
returned to the pool when requests overlap — consistent with the known BaseHTTPMiddleware + async-generator
dependency interaction (`app/middleware.py:8-23`, `app/main.py:20-21`). Impact: on any backend, the read path
degrades to 0 throughput once the pool (10+5) is exhausted; `pool_timeout=30` then turns every request into a 30s stall + 500.

**Ledger row:** `01-fastapi-live-metrics-ingest.md` — **FA-9** (new). Fix: FA-4 (drop BaseHTTPMiddleware) + validate with the repro.

**Post-fix re-check (2026-08-01, sqlite dev stack):** `BaseHTTPMiddleware` is gone (pure ASGI middleware) and
single-request latency is unchanged (**wide window 0.62–0.66s; narrow 1h window 18ms; 3× concurrent wide all 200,
pool alive**), but **8× concurrent wide-window GETs still hang >20s and wedge the pool — FA-9 NOT closed.** The
wedge reproduces without HTTP: 8 raw aiosqlite sessions hang, and even **plain `sqlite3` threads doing a
195k-row `fetchall` hang with 4+ threads while `count(*)` with 4 threads completes in 72ms** (WAL mode does not
help; 2 threads OK). Conclusion: the original middleware diagnosis was wrong — this is a sqlite-level
large-result-set fetch concurrency problem on the dev stack. The real fix is the FA-1 Postgres pushdown
(`percentile_cont` — 1 row back instead of ~200k) plus Postgres for the read path; FA-9/FA-1 validation stays
gated on a Postgres host.

### 1d. CPU hot path (in-process, `bench/microbench.py`)

| Operation | Cost |
|---|---|
| `normalize_route` (2 regex passes) | 0.8 µs/label |
| Pydantic validate, 100-sample batch | 101.5 µs/batch |
| App-side percentile sort, 100k floats | **12.6 ms** |
| App-side percentile sort, 1M floats | **216.9 ms** |

---

## 2. django-flash-sale-inventory

Harness: `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/bench/`
(`seed.py` — 1 ACTIVE sale, 20 SKUs, 3 warehouses, 60 stock rows; `availability.js`, `rollup.js`, `reserve_bench.py`).
DB: `db.sqlite3` (created via `python3 manage.py migrate --run-syncdb`). Server: `runserver 8102 --noreload`.

### 2a. `GET /api/warehouses/WH{n}/rollup/` — target 300/s, 20s

| Metric | Value |
|---|---|
| Achieved throughput | **174.9 req/s** (3581 reqs) |
| Latency | avg 545ms · **med 524ms** · p90 683ms · p95 796ms · max 1.71s |
| Errors | 0.00% |

### 2b. `POST /api/availability/batch/` (10 SKUs) — target 300/s, 20s

| Metric | Value |
|---|---|
| Achieved throughput | **123.4 req/s** (2595 reqs) |
| Latency | avg 772ms · **med 728ms** · p90 1.05s · p95 1.11s · max 1.31s |
| Errors | 0.00% |

### 2c. Reserve hot path (service layer, `reserve_bench.py`, sqlite, warm)

| Operation | Time | Queries |
|---|---|---|
| `reserve()` 1 line | 6.6 ms | **7** |
| `reserve()` 5 lines | 13.3 ms | **27** |
| `reserve()` 20 lines | 34.0 ms | **102** (5N+2 pattern, DJ-1/DJ-2) |
| `reserve()` + `confirm()` 20 lines | 68.4 ms | **166** |
| `release_expired()` (empty) | 0.8 ms | 2 |

**Post-fix (2026-08-01, `bench/reserve_bench.py`, sqlite, warm):**

| Operation | Time | Queries |
|---|---|---|
| `reserve()` 1 line | **5.4 ms** (was 6.6) | **7** (unchanged) |
| `reserve()` 5 lines | **8.2 ms** (was 13.3) | **15** (was 27) |
| `reserve()` 20 lines | **17.0 ms** (was 34.0) | **45** (was 102, DJ-1/DJ-2) |
| `reserve()` + `confirm()` 20 lines | **32.5 ms** (was 68.4) | **72** (was 166) |
| `release_expired()` (empty) | **0.3 ms** | 2 |

k6 after (fresh server, 3-run medians, 0% errors): availability **~136.7 req/s** (was 123.4);
rollup **~174.9 req/s** (unchanged). Tests: **31/31 pass** (was 24/25; sqlite concurrency test fixed
via `sqlite3_immediate` BEGIN IMMEDIATE backend).

---

## 3. flask-partner-webhook-relay

Harness: `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/bench/`
(`seed.py`, `ingest.js`, `delivery_bench.py`). DB: `instance/bench_relay.db` (Flask-SQLAlchemy instance path).
Server: Werkzeug dev server, port 8103, threaded.

### 3a. `POST /api/v1/webhooks` — target 200/s, 20s

| Metric | Value |
|---|---|
| Achieved throughput | **116.0 req/s** (2438 reqs) |
| Latency | avg 807ms · **med 619ms** · p90 1.64s · p95 2.04s · max 5.36s |
| Errors | 0.00% |

### 3b. Delivery worker vs mock partner (200ms latency, `delivery_bench.py`)

| Operation | Value |
|---|---|
| `run_once()` — 50 items, **sequential** | **10.40s median** (208ms/item ≈ partner latency + overhead) |
| Theoretical with `DELIVERY_MAX_CONCURRENCY=10` (config exists, unused) | ≈ 1.0–1.2s |

Command: `python3 -m bench.delivery_bench` (mock `ThreadingHTTPServer` on 127.0.0.1:8200 sleeps 200ms; 3 rounds, median).
The 10.4s quantifies FL-1: one slow partner serializes the whole queue.

**Post-fix (2026-08-01, `python3 -m bench.delivery_bench`):** 50-item fan-out **1.05s median** (was 10.40s, 9.9×;
at the ⌈50/10⌉ × 200ms concurrency floor). Ingest k6 after (fresh server): **121.3 req/s / 629ms med** and
**146.0 req/s / 477ms med** (was 116.0 req/s / 619ms; 0% errors). Maintenance (100k-row fixtures):
purge **4.80s**, redrive **0.46s with flat RSS**. Tests: **24 pass + 1 skip + 1 xfail** (was 7 + 1 xfail;
skip = FL-2 disjoint-claim proof, Postgres-gated).

---

## 4. Cross-project summary

| Project | Endpoint / op | Baseline result | After (2026-08-01) | Ledger rows to improve it |
|---|---|---|---|---|
| fastapi | ingest (100-sample batch) | 29 req/s, 2.94s med | not re-measured (Postgres-gated) | FA-2/3/5, FA-9 (pool) |
| fastapi | percentiles (267k rows) | 0.6s single; **0 req/s concurrent** (pool exhaust) | 0.66s single; **8× concurrent still wedges** (sqlite dev stack) | FA-9, FA-1, FA-2 |
| fastapi | app-side sort 1M rows | 217 ms | n/a (FA-1 pushdown is Postgres-only) | FA-1 |
| django | rollup | 175 req/s, 524ms med | 175 req/s (flat) | DJ-6 |
| django | batch availability | 123 req/s, 728ms med | **~137 req/s** | DJ-6 |
| django | reserve 20-line order | 34ms / **102 queries** | **17ms / 45 queries** | DJ-1, DJ-2 |
| flask | webhook ingest | 116 req/s, 619ms med | **121–146 req/s, 477–629ms med** | FL-4 |
| flask | delivery fan-out 50 items | **10.40s sequential** | **1.05s concurrent (9.9×)** | FL-1, FL-2 |

## 5. Re-run procedure

1. Start each server exactly as in the section headers (env vars included; fastapi needs `bench/seed_db.py` first).
2. `k6 run bench/<scenario>.js --summary-export=/tmp/opencode/k6_<name>.json` per scenario; record the summary metrics.
3. In-process benches: `python3 -m bench.microbench.py` (fastapi), `python3 -m bench.reserve_bench` (django),
   `python3 -m bench.delivery_bench` (flask).
4. Keep release measurements distinct from dev-loop measurements; record cold-vs-warm state next to each number.
