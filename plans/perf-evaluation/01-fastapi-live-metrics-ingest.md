# Phase 1 — fastapi-live-metrics-ingest (Evaluation + Improvements)

<a id="FA-9"></a>
## FA-9 [HIGH] — Concurrent DB-backed requests exhaust the async engine pool (discovered by baseline benchmarks, 2026-07-31)

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/dependencies.py:9-12` + `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/middleware.py:8-23` + `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/main.py:20-21`

**Current code:**
```python
# dependencies.py L9-12 — async-generator session dependency
async def get_session(request: Request) -> AsyncSession:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session
```
```python
# middleware.py L8-23 — both middlewares extend BaseHTTPMiddleware
class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)      # endpoint runs in a spawned task
        ...
```

**Evidence (`/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/benchmarks.md` §1c):** 8 concurrent
GETs to `/percentiles` (fresh uvicorn, 6s client timeout) → all 8 ReadTimeout; a subsequent single request hangs >40s
(pool dead). `/health` at 20 VUs → 493 req/s (HTTP stack fine). Raw SQLAlchemy sessions: 20 concurrent SELECTs in 1.9s
(DB + pool fine). Conclusion: sessions checked out through the dependency are not returned when requests overlap —
consistent with the known BaseHTTPMiddleware × async-generator dependency interaction. On any backend this collapses
the read path to 0 throughput once the pool (10+5, config.py:6-7) is exhausted; `pool_timeout=30` (config.py:8) turns
every subsequent request into a 30s stall + 500.

**Change to:** apply FA-4 (replace `BaseHTTPMiddleware` with pure ASGI middleware) and re-run the repro + `k6 run bench/percentiles.js`.

- [ ] **FA-9** — apply FA-4; Expected: 8 concurrent percentiles requests complete <1s; 20-VU k6 run holds throughput ≈ 1/single-request-latency; no 500s. Proof: repro in `benchmarks.md` §1c re-run green + `k6 run bench/percentiles.js` shows non-zero req/s; `python3 -m pytest -q` still 11 pass.

---

> **Canonical ledger:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/plans/perf-evaluation/README.md`
> **Status:** Evaluation complete; improvements not started
> **Project root:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest`
> **Baseline:** `python3 -m pytest -q` → 11 passed (0.07s)

Hot paths: `POST /api/v1/ingest` (batch write), `GET /percentiles|top-routes|error-rates` (read), TTL cleanup task.

---

<a id="FA-1"></a>
## FA-1 [HIGH] — Percentiles materialize every row in Python and sort app-side

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/services/aggregation.py:11-31` (hot lines `:18`, `:22`)

**Current code:**
```python
async def get_percentiles(self, *, tenant_id: int, window_start: datetime, window_end: datetime) -> dict:
    query = select(MetricSample.latency_ms).where(          # L12
        MetricSample.tenant_id == tenant_id,
        MetricSample.timestamp >= window_start,
        MetricSample.timestamp < window_end,
    )
    result = await self.session.execute(query)              # L17
    rows = result.scalars().all()                           # L18 <-- ALL rows transferred to Python
    total_samples = len(rows)                               # L19
    if total_samples == 0:                                  # L20
        return {"p50": None, "p95": None, "p99": None, "total_samples": 0}
    sorted_vals = sorted(rows)                              # L22 <-- O(N log N) app-side sort
    p50 = sorted_vals[total_samples // 2]                   # L23
    p95 = sorted_vals[int(total_samples * 0.95)]            # L24
    p99 = sorted_vals[int(total_samples * 0.99)]            # L25
    return {                                                # L26-31
        "p50": round(p50, 2),
        "p95": round(p95, 2),
        "p99": round(p99, 2),
        "total_samples": total_samples,
    }
```

**Problem:** With 24h retention at high QPS the table holds millions of rows; every dashboard request transfers all
`latency_ms` values to the app and sorts them. `WindowAggregate` (`app/models.py:37-49`) exists but is never written,
so there is no rollup escape hatch.

**Change to** (push percentiles into Postgres, 1 row returned):
```python
async def get_percentiles(self, *, tenant_id: int, window_start: datetime, window_end: datetime) -> dict:
    query = select(
        func.percentile_cont(0.50).within_group(MetricSample.latency_ms.asc()).label("p50"),
        func.percentile_cont(0.95).within_group(MetricSample.latency_ms.asc()).label("p95"),
        func.percentile_cont(0.99).within_group(MetricSample.latency_ms.asc()).label("p99"),
        func.count().label("total_samples"),
    ).where(
        MetricSample.tenant_id == tenant_id,
        MetricSample.timestamp >= window_start,
        MetricSample.timestamp < window_end,
    )
    row = (await self.session.execute(query)).one()
    return {
        "p50": round(row.p50, 2) if row.p50 is not None else None,
        "p95": round(row.p95, 2) if row.p95 is not None else None,
        "p99": round(row.p99, 2) if row.p99 is not None else None,
        "total_samples": row.total_samples,
    }
```

- [ ] **FA-1** — apply change; Expected: constant row transfer (1 row) regardless of window size; SQL semantics for interpolation. Proof: `EXPLAIN ANALYZE` on 1M-row fixture; `python3 -m pytest -q` still 11 pass.

---

<a id="FA-2"></a>
## FA-2 [HIGH] — No composite index on `MetricSample(tenant_id, timestamp)`

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/models.py:24-35`

**Current code:**
```python
class MetricSample(Base):
    __tablename__ = "metric_samples"

    id: Mapped[int] = mapped_column(primary_key=True)                       # L27
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))        # L28
    batch_id: Mapped[int] = mapped_column(ForeignKey("ingest_batches.id"))  # L29
    route_label: Mapped[str] = mapped_column(String(512))                   # L30
    latency_ms: Mapped[float] = mapped_column(Float)                        # L31
    status_code: Mapped[int] = mapped_column(Integer)                       # L32
    ua_class: Mapped[str] = mapped_column(String(64))                       # L33
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))    # L34
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # L35
```

**Problem:** Every query in `aggregation.py:12-16, 33-44, 56-63` filters on exactly these columns; without the
index every read is a full scan + sort (top-routes GROUP BY).

**Change to:**
```python
from sqlalchemy import Index  # add to L2 import list

class MetricSample(Base):
    __tablename__ = "metric_samples"
    # ... columns unchanged (L27-35) ...

    __table_args__ = (
        Index("ix_metric_samples_tenant_ts", "tenant_id", "timestamp"),
    )
```

- [ ] **FA-2** — apply change; Expected: percentiles/top-routes/error-rates become index range scans. Proof: `EXPLAIN` shows Index Scan, no Seq Scan, on warm cache. Do this before FA-1 validation.

---

<a id="FA-3"></a>
## FA-3 [HIGH] — TTL cleanup full-table-scans an unindexed `created_at` every 5 min

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/tasks.py:32-48` (hot lines `:39-40`)

**Current code:**
```python
async def _ttl_cleanup_loop(self, session_factory):
    while not self._shutdown_event.is_set():
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.retention_hours)
            async with session_factory() as session:
                while True:
                    stmt = delete(MetricSample).where(
                        MetricSample.created_at < cutoff      # L39 <-- full scan, created_at unindexed
                    ).limit(500)                              # L40
                    result = await session.execute(stmt)
                    await session.commit()
                    if result.rowcount == 0:
                        break
                    logger.info("Deleted %d old metric samples", result.rowcount)
        except Exception as e:
            logger.error("TTL cleanup error: %s", e)
        await asyncio.sleep(300)                              # L48
```

**Problem:** No index on `created_at` (`models.py:35` has only `server_default`) → every cleanup run is O(table);
500-row batches with commit-per-batch also fight ingest writes.

**Change to** (index first; partition for >10M rows):
```python
__table_args__ = (
    Index("ix_metric_samples_tenant_ts", "tenant_id", "timestamp"),   # from FA-2
    Index("ix_metric_samples_created_at", "created_at"),              # new
)
```
For multi-10M-row tables prefer native partition-by-day on `timestamp` and `DROP PARTITION` instead of batched DELETE.

- [ ] **FA-3** — apply index (+ partition if scale demands); Expected: cleanup cost bounded to the expired tail. Proof: cleanup loop duration on 2M-row fixture before/after; `EXPLAIN` on the DELETE predicate shows Index Scan.

---

<a id="FA-4"></a>
## FA-4 [MED] — `BaseHTTPMiddleware` on every request (known FastAPI hot-path footgun)

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/middleware.py:3,8,18` + registration `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/main.py:20-21`

**Current code:**
```python
from starlette.middleware.base import BaseHTTPMiddleware   # L3

class RequestTimingMiddleware(BaseHTTPMiddleware):          # L8
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()                            # L10
        response = await call_next(request)                 # L11
        duration_ms = (time.monotonic() - start) * 1000     # L12
        response.headers["X-Request-Duration-Ms"] = str(int(duration_ms))  # L13
        if duration_ms > 1000:                              # L14
            logger.warning("Slow request: %s %s took %.0fms", request.method, request.url.path, duration_ms)
        return response

class TenantHeaderMiddleware(BaseHTTPMiddleware):           # L18
    async def dispatch(self, request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")      # L20
        if tenant_id:
            request.state.tenant_id = tenant_id             # L22
        return await call_next(request)                     # L23
```

**Problem:** `BaseHTTPMiddleware` wraps the ASGI app (extra task + streaming buffer) and is a documented
Starlette/uvicorn hot-path overhead.

**Change to** (pure ASGI middleware):
```python
class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.monotonic()

        async def send_with_timing(message):
            if message["type"] == "http.response.start":
                duration_ms = int((time.monotonic() - start) * 1000)
                message.setdefault("headers", []).append(
                    (b"x-request-duration-ms", str(duration_ms).encode())
                )
                if duration_ms > 1000:
                    logger.warning("Slow request: %s %s took %dms", scope["method"], scope["path"], duration_ms)
            await send(message)

        await self.app(scope, receive, send_with_timing)
```
For `TenantHeaderMiddleware`, either fold the header into `scope["state"]["tenant_id"]` in the same pure-ASGI
middleware, or delete it and read the header in the dependency (`app/dependencies.py:14-22`) where it is already used.

- [ ] **FA-4** — apply change; Expected: per-request overhead drops to header-set + monotonic clock; identical header/behavior. Proof: `pytest` passes; compare `X-Request-Duration-Ms` overhead across 1k sequential requests before/after.

---

<a id="FA-5"></a>
## FA-5 [MED] — No connection recycling / statement timeout on the async engine

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/database.py:4-12`

**Current code:**
```python
def create_engine(settings: Settings):
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.pool_size,           # L8
        max_overflow=settings.max_overflow,     # L9
        pool_timeout=settings.pool_timeout,     # L10
        pool_pre_ping=settings.pool_pre_ping,   # L11
    )
```

**Problem:** A runaway aggregation (FA-1) can pin pool connections indefinitely; long-lived asyncpg connections
are never recycled.

**Change to:**
```python
def create_engine(settings: Settings):
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_pre_ping=settings.pool_pre_ping,
        pool_recycle=1800,
        connect_args={"server_settings": {"statement_timeout": "30000"}},
    )
```

- [ ] **FA-5** — apply change; Expected: no connection pinned >30s; no zombie connections after 30min. Proof: 1h soak under load; assert zero `pool_timeout` errors in logs.

---

<a id="FA-6"></a>
## FA-6 [LOW] — Per-sample double regex pass in the ingest hot path

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/services/ingest.py:14-20`

**Current code:**
```python
_route_cleanup_re = re.compile(r"\?.*$")          # L14
_numeric_segment_re = re.compile(r"/\d+(/|$)")    # L15

def normalize_route(label: str) -> str:           # L17
    label = _route_cleanup_re.sub("", label)
    label = _numeric_segment_re.sub(r"/{id}\1", label)
    return label
```

**Problem:** Two `re.sub` passes per sample (up to 1000 samples/batch). Module-level compilation is good;
collapse into one pass.

**Change to:**
```python
_route_re = re.compile(r"(\?.*$)|(/\d+(/|$))")    # one compiled pass

def _normalize(match: re.Match) -> str:
    if match.group(2):                            # numeric segment -> /{id}/ or /{id}
        return "/{id}" + match.group(3)
    return ""                                     # query string -> dropped

def normalize_route(label: str) -> str:
    return _route_re.sub(_normalize, label)
```

- [ ] **FA-6** — apply change; Expected: identical normalized output, half the regex passes. Proof: existing `normalize_route` unit tests + micro-benchmark 100k labels.

---

<a id="FA-7"></a>
## FA-7 [LOW] — Idempotency is SELECT-then-INSERT; concurrent duplicates burn extra round-trips

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/services/ingest.py:27-55` (hot lines `:29`, `:46`, `:48`)

**Current code:**
```python
async def process_batch(self, *, tenant_id: int, request: IngestRequest) -> IngestResponse:
    if request.idempotency_key:
        existing = await self._find_batch_by_key(request.idempotency_key)   # L29 SELECT
        if existing:
            return IngestResponse(accepted=existing.sample_count, batch_id=existing.id, already_processed=True)

    batch = IngestBatch(
        tenant_id=tenant_id, idempotency_key=request.idempotency_key, sample_count=len(request.samples),
    )
    self.session.add(batch)

    try:
        await self.session.flush()                                          # L45 INSERT
    except IntegrityError:                                                  # L46 <-- race: concurrent duplicate
        await self.session.rollback()
        existing = await self._find_batch_by_key(request.idempotency_key)   # L48 second SELECT
        ...
```

**Problem:** Two concurrent batches with the same key both pass the SELECT; one pays IntegrityError + rollback + re-SELECT.

**Change to** (Postgres `INSERT ... ON CONFLICT` — the batch row only; sample insert loop unchanged):
```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

if request.idempotency_key:
    stmt = (
        pg_insert(IngestBatch)
        .values(tenant_id=tenant_id, idempotency_key=request.idempotency_key,
                sample_count=len(request.samples))
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(IngestBatch.id, IngestBatch.sample_count)
    )
    row = (await self.session.execute(stmt)).first()
    await self.session.commit()
    if row:
        return IngestResponse(accepted=row.sample_count, batch_id=row.id, already_processed=False)
    existing = await self._find_batch_by_key(request.idempotency_key)
    return IngestResponse(accepted=existing.sample_count, batch_id=existing.id, already_processed=True)
```

- [ ] **FA-7** — apply change; Expected: duplicate batches resolved in one round-trip. Proof: 10 concurrent coroutines with the same key → one batch, no IntegrityError surfaced.

---

<a id="FA-8"></a>
## FA-8 [MED] — Vendor export path is dead code with latent `AttributeError`

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/tasks.py:50-56` (stub) + `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/fastapi-live-metrics-ingest/app/services/vendor_export.py:19-93` (hot lines `:80-84`, `:88`, `:20-24`)

**Current code:**
```python
# tasks.py L50-56 — loop never calls VendorExportService
async def _vendor_export_loop(self, session_factory):
    while not self._shutdown_event.is_set():
        try:
            logger.debug("Vendor export loop tick")    # L53 <-- stub, does nothing
        except Exception as e:
            logger.error("Vendor export loop error: %s", e)
        await asyncio.sleep(60)

# vendor_export.py L78-84 — writes columns that do not exist on the model
if last_exception is None:
    job.status = "DONE"
    job.completed_at = datetime.now(timezone.utc)      # L80 <-- AttributeError: no such column
    job.sample_count = len(routes)                     # L81 <-- AttributeError
else:
    job.status = "FAILED"
    job.error_message = str(last_exception)            # L84 <-- AttributeError
```

**Problem:** The loop is a stub, so exports never run; if wired, lines 80-84/88 crash because the model
(`app/models.py:51-60`) has no `completed_at`/`sample_count`/`error_message`. Also `window_start`/`window_end`
are dropped when constructing the job (vendor_export.py:20-24).

**Change to** (model first):
```python
class VendorExportJob(Base):
    __tablename__ = "vendor_export_jobs"
    # ... existing columns (models.py:54-60) ...
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # new
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)                        # new
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)                          # new
```
and in `export_aggregates` persist the window:
```python
job = VendorExportJob(
    tenant_id=tenant_id, status="PENDING",
    window_start=window_start, window_end=window_end,     # fix L20-24
    created_at=datetime.now(timezone.utc),
)
```
and wire the loop (bounded batch of pending jobs per tick, one `httpx.AsyncClient` per tick):
```python
async def _vendor_export_loop(self, session_factory):
    while not self._shutdown_event.is_set():
        try:
            async with session_factory() as session, httpx.AsyncClient(timeout=self.settings.vendor_timeout_seconds) as client:
                service = VendorExportService(session, self.settings, client)
                # SELECT pending jobs with window_end <= now, call service.export_aggregates(...)
        except Exception as e:
            logger.error("Vendor export loop error: %s", e)
        await asyncio.sleep(60)
```

- [ ] **FA-8** — wire or delete; Expected: no AttributeError path; exports run on schedule with bounded payload. Proof: integration test invoking the loop with a mock httpx client; assert job rows reach DONE/FAILED with persisted fields.
