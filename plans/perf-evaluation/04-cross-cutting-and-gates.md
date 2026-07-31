# Phase 4+5 — Cross-Cutting, Observability, Deployment & Closure Gates

> **Canonical ledger:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/plans/perf-evaluation/README.md`
> **Status:** Not started
> **Applies to:** all three projects (fastapi-live-metrics-ingest, django-flash-sale-inventory, flask-partner-webhook-relay)

---

## Phase 4 — Cross-cutting items

<a id="XC-1"></a>
### XC-1 [HIGH] — Add a benchmark harness per project (no load benchmark exists anywhere)

**Problem:** None of the three projects has a load benchmark; unit tests pass while the hot paths (verified in
phases 1-3) remain unmeasured. Every performance row needs a before/after number to close.

**Change to** — one scenario file per project (k6 preferred; locust also fine):

```javascript
// fastapi-live-metrics-ingest/bench/ingest.js (k6)
import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: {
    ingest: { executor: "constant-arrival-rate", rate: 2000, duration: "2m", preAllocatedVUs: 50 },
  },
  thresholds: { http_req_duration: ["p(99)<150"] },
};

const body = {
  idempotency_key: "bench-batch",
  samples: Array.from({ length: 100 }, (_, i) => ({
    route_label: `/api/orders/${i % 50}/items/${i}`,
    latency_ms: Math.random() * 500,
    status_code: 200,
    ua_class: "bench",
    timestamp: new Date().toISOString(),
  })),
};

export default function () {
  const res = http.post("http://localhost:8000/api/v1/ingest", JSON.stringify(body), {
    headers: { "Content-Type": "application/json", "X-Tenant-Id": "1" },
  });
  check(res, { "201 accepted": (r) => r.status === 201 });
}
```

- [x] **XC-1** — done 2026-07-31: harness files added per project (`fastapi-live-metrics-ingest/bench/`, `django-flash-sale-inventory/bench/`, `flask-partner-webhook-relay/bench/`) and baselines recorded in `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/benchmarks.md` (dev-stack: sqlite + dev servers; Postgres/prod gates still pending). Re-run the bench scenarios before flipping any performance row `[x]`; keep release measurements distinct from dev-loop measurements.

<a id="XC-2"></a>
### XC-2 [MED] — Real connection pooling + timeouts on all three DB paths

**Problem:** Pool settings are only configured on the FastAPI engine (see `01-fastapi-live-metrics-ingest.md#FA-5`);
Django defaults to per-request connections and Flask to none.

**Change to:**
```python
# django — flash_sale/settings.py DATABASES["default"] (see 02 file, DJ-7)
'CONN_MAX_AGE': 60,
'CONN_HEALTH_CHECKS': True,
```
```python
# flask — app/config.py (see 03 file, FL-7)
SQLALCHEMY_ENGINE_OPTIONS = {"pool_size": 10, "max_overflow": 5, "pool_pre_ping": True}
```

- [ ] **XC-2** — apply all three; Expected: no pool errors during 1h soak at target load. Proof: 1h soak logs show zero `pool_timeout` / `connection is closed` errors.

<a id="XC-3"></a>
### XC-3 [MED] — Production server configuration (documented commands)

**Problem:** All projects default to dev servers (`uvicorn app.main:app`, `python manage.py runserver`, `flask run`).

**Change to** — document in each project README:
```bash
# fastapi (per CPU core; asyncio loop; keep DB pool >= workers)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers $(nproc) --loop asyncio --http httptools

# django
gunicorn flash_sale.wsgi:application --workers $(nproc) --threads 4 --timeout 60

# flask web process (worker runs as its own process via `flask run-worker`)
gunicorn "app:create_app()" --workers 4 --timeout 60
```

- [ ] **XC-3** — document commands; Expected: single worker/poll loop separation for flask (web vs `run-worker`), multi-process fastapi/django. Proof: `pytest` green + commands present in READMEs.

<a id="XC-4"></a>
### XC-4 [MED] — Observability: metric endpoints per project

**Problem:** Timing middlewares only stamp `X-Request-Duration-Ms` (fastapi `app/middleware.py:13`,
django `inventory/middleware.py:12`); no counters, histograms, or queue-depth exposure exists.

**Change to** — add a Prometheus `/metrics` endpoint per project (e.g. prometheus-fastapi-instrumentator /
django-prometheus / prometheus-flask-exporter):
```python
# shape: request duration histogram per route, reservation success/fail counter,
# delivery queue depth gauge (reuse cli.py:68-78 logic), vendor export job status gauge
```

- [ ] **XC-4** — add metrics endpoints; Expected: p50/p95/p99 histograms + queue gauges scrapeable. Proof: scrape `/metrics` during a synthetic load run shows non-empty histograms.

<a id="XC-5"></a>
### XC-5 [LOW] — Close the 4 recorded semgrep findings (security hardening, not perf)

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/semgrep_results.txt` +
each `plans/semgrep-findings.md`

**Current code:**
```python
# django — inventory/views.py:20
@csrf_exempt                       # L20 <-- python.django.security.audit.csrf-exempt.no-csrf-exempt
@require_POST
def batch_availability(request):
```
```dockerfile
# all three Dockerfiles — dockerfile.security.missing-user.missing-user
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]   # fastapi Dockerfile:12
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]                 # django Dockerfile:16
CMD ["flask", "run", "--host", "0.0.0.0", "--port", "8000"]              # flask Dockerfile:15
```

**Change to** — drop `@csrf_exempt` (use `@csrf_protect` + JSON header auth, or Django REST-style token auth)
and add `USER non-root` before each `CMD`.

- [ ] **XC-5** — apply; Expected: 0 blocking findings on re-scan. Proof: `semgrep --config=auto` → 0 findings (command + output recorded in this row).

---

## Phase 5 — Closure gates

A row closes only with measured evidence. Commands and targets (warm cache unless stated):

| Project | Command | Gate |
|---|---|---|
| fastapi | `cd /home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest && python3 -m pytest -q` | 11 pass |
| fastapi (load) | `k6 run bench/ingest.js` | 2k RPS ingest, p99 < 150ms; percentile endpoint p99 < 300ms on 1M-row fixture |
| django | `cd /home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory && python3 manage.py test` | 25/25 pass on Postgres (currently 24/25 on sqlite) |
| django (load) | k6 reservation scenario | 200 concurrent users, 0 IntegrityError/500, stock never negative |
| flask | `cd /home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay && python3 -m pytest -q` | 7 pass + 1 xfail |
| flask (load) | k6 ingest + fan-out scenario | 500 events/s ingest; 50-outbox fan-out ≤ 5s wall-clock with a 200ms mock partner |

Record every command + outcome inline next to the row it validates; successful test execution alone is not benchmark proof.

---

## Dependencies

- Phase 1 rows depend on: a Postgres instance for fixture/EXPLAIN work (FA-1..FA-3, FA-5); no code dependency between FA-1 and FA-2 (both touch the read path — do FA-2 first: the index helps regardless).
- Phase 2 rows depend on: Postgres active (**DJ-7**) **before** DJ-3/DJ-4 validation (row locks are the premise); DJ-1 → DJ-2 (same function, do hoisting first).
- Phase 3 rows depend on: **FL-2** (atomic claim) **before** FL-1 (concurrency) — threads without atomic claim double-deliver; FL-3 is independent; FL-7 before load validation.
- Phase 4: XC-1 is the measuring stick for all closure gates; XC-5 is independent security hardening and can run anytime.

## Handoff note

When a row is implemented and validated, flip `[ ]` → `[x]` and record the exact command + metric inline.
Deferrals go `[~]` with the reason and next gate. Do not mark any performance row `[x]` based on tests alone —
the load-benchmark proof in Phase 5 is required.
