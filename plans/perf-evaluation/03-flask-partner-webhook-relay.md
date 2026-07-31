# Phase 3 — flask-partner-webhook-relay (Evaluation + Improvements)

> **Canonical ledger:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/plans/perf-evaluation/README.md`
> **Status:** Improvements implemented + verified 2026-08-01 (FL-1..FL-8; FL-2, FL-7 applied, Postgres-gated [~])
> **Project root:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay`
> **Baseline:** `python3 -m pytest -q` → 7 passed, 1 xfailed (0.49s)

Hot paths: `POST /api/v1/webhooks` (ingest + fan-out rows), `run-worker` delivery loop (blocking HTTP fan-out with retries).

---

<a id="FL-1"></a>
## FL-1 [HIGH] — Delivery is strictly sequential; `DELIVERY_MAX_CONCURRENCY` is never used

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/services/delivery.py:115-122` + `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/cli.py:19-23` (config: `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/config.py:13`)

**Current code:**
```python
# delivery.py L115-122
def run_once(self) -> int:
    with self.app.app_context():
        items = self.claim_work(batch_size=self.app.config["DELIVERY_CLAIM_BATCH_SIZE"])  # L117
        delivered = 0
        for outbox in items:                 # L119 <-- strictly sequential
            self.deliver(outbox)             # L120 blocking requests.post per item
            delivered += 1
        return delivered

# config.py L13 — configured but never read anywhere
DELIVERY_MAX_CONCURRENCY = int(os.getenv("DELIVERY_MAX_CONCURRENCY", "10"))
# models.py L22 — concurrency_cap also never read
concurrency_cap = db.Column(db.Integer, default=5)
```

**Problem:** With `DELIVERY_TIMEOUT_CONNECT=10` / `DELIVERY_TIMEOUT_READ=30` (config.py:9-10), one slow
partner stalls the whole queue up to 40s per attempt × batch size. Fan-out wall-clock = Σ partners, not slowest.

**Change to:**
```python
from concurrent.futures import ThreadPoolExecutor

def run_once(self) -> int:
    with self.app.app_context():
        items = self.claim_work(batch_size=self.app.config["DELIVERY_CLAIM_BATCH_SIZE"])
        if not items:
            return 0
        max_workers = self.app.config["DELIVERY_MAX_CONCURRENCY"]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            pool.map(self.deliver, items)    # concurrent fan-out; per-endpoint cap via semaphores
        return len(items)
```
**Caveat:** `self.deliver` uses the Flask-SQLAlchemy thread-local session — run each `deliver` inside
`with self.app.app_context():` (or give each worker thread its own session) to avoid cross-thread session sharing.

- [x] **FL-1** — apply change; Expected: fan-out wall-clock ≈ slowest partner, not Σ partners; the never-used config keys (config.py:13, models.py:22) take effect. Proof: benchmark — 50 outbox items to a 200ms mock endpoint: total ≤ ~1s (was ~10s+); unit test asserting per-endpoint concurrency ≤ cap. **Requires FL-2 first (atomic claim).** — **Verified 2026-08-01:** `ThreadPoolExecutor(max_workers=DELIVERY_MAX_CONCURRENCY=10)` in `delivery.py:177-188` + per-endpoint `BoundedSemaphore` (`delivery.py:41-48,88`); `python3 -m bench.delivery_bench` → 50-item fan-out **1.07s median** (evidence 1.12s) vs **10.40s baseline** = 9.7× speedup, at theoretical floor (⌈50/10⌉×200ms=1.0s); `test_pool_bounded_by_delivery_max_concurrency` + `test_per_endpoint_concurrency_respects_cap` PASSED. Baseline: benchmarks.md §3a.

---

<a id="FL-2"></a>
## FL-2 [HIGH] — `claim_work` is not atomic → concurrent workers double-deliver

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/services/delivery.py:33-51`

**Current code:**
```python
def claim_work(self, batch_size: int = 50) -> list:
    now = datetime.now(timezone.utc)
    items = DeliveryOutbox.query.filter(          # L35 SELECT — no row lock
        DeliveryOutbox.status.in_(["PENDING", "FAILED"]),
        DeliveryOutbox.next_attempt_at <= now,
    ).order_by(DeliveryOutbox.next_attempt_at.asc()).limit(batch_size).all()  # L38
    filtered = []
    for item in items:
        endpoint = item.partner_endpoint          # L42 (see FL-3)
        if not endpoint.is_active:
            continue
        if endpoint.circuit_until and endpoint.circuit_until > now:
            continue
        item.status = "IN_FLIGHT"                 # L47 <-- second statement, no lock held
        filtered.append(item)
    db.session.commit()                           # L50
    return filtered
```

**Problem:** Two workers (or threads after FL-1) can both SELECT the same rows before either commits the
`IN_FLIGHT` update → the same webhook is POSTed to the partner twice.

**Change to** (lock-and-claim in one transaction):
```python
def claim_work(self, batch_size: int = 50) -> list:
    now = datetime.now(timezone.utc)
    items = (
        DeliveryOutbox.query
        .filter(
            DeliveryOutbox.status.in_(["PENDING", "FAILED"]),
            DeliveryOutbox.next_attempt_at <= now,
        )
        .order_by(DeliveryOutbox.next_attempt_at.asc())
        .with_for_update(skip_locked=True)        # L38 <-- atomically claim only unlocked rows
        .limit(batch_size)
        .all()
    )
    filtered = []
    for item in items:
        endpoint = item.partner_endpoint
        if not endpoint.is_active or (endpoint.circuit_until and endpoint.circuit_until > now):
            continue
        item.status = "IN_FLIGHT"
        filtered.append(item)
    db.session.commit()                           # L50 commit releases locks + persists claim
    return filtered
```

- [~] **FL-2** — apply change; Expected: exactly-one claim per row across concurrent workers. Proof: two concurrent workers over a 200-row queue claim disjoint sets (union == 200, no overlap). — **Applied + verified in code 2026-08-01, proof deferred:** atomic claim applied (`delivery.py:62` `.with_for_update(skip_locked=True)`, lock+claim in one txn, commit at L77); disjoint-claim proof needs Postgres row locks — sqlite dialect emits FOR UPDATE as a no-op; `test_concurrent_claim_is_disjoint` self-skips when `db.engine.dialect.name != "postgresql"` (`tests/test_delivery_worker.py:46-72`, locally SKIPPED); next gate: Postgres host.

---

<a id="FL-3"></a>
## FL-3 [MED] — N+1 on relationships in the worker (lazy loads per row)

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/services/delivery.py:35-38,42,54-55`

**Current code:**
```python
items = DeliveryOutbox.query.filter(...).order_by(...).limit(batch_size).all()  # L35-38
...
endpoint = item.partner_endpoint                    # L42 <-- lazy load per row
...
def deliver(self, outbox: DeliveryOutbox) -> DeliveryAttempt:
    endpoint = outbox.partner_endpoint              # L54 <-- lazy load
    inbound = outbox.inbound_event                  # L55 <-- lazy load
```

**Problem:** Batch of 50 = 100+ extra queries (`partner_endpoint` per claim row, `partner_endpoint` +
`inbound_event` per deliver).

**Change to:**
```python
from sqlalchemy.orm import joinedload

items = (
    DeliveryOutbox.query
    .options(joinedload(DeliveryOutbox.partner_endpoint).joinedload(PartnerEndpoint.partner),
             joinedload(DeliveryOutbox.inbound_event))   # L35-38
    .filter(...)
    .order_by(...)
    .limit(batch_size)
    .all()
)
```

- [x] **FL-3** — apply change; Expected: batch of 50 → ~1 query instead of 100+. Proof: assert query count in a worker test via SQLAlchemy event logging. — **Verified 2026-08-01:** `joinedload(partner_endpoint).joinedload(partner)` + `joinedload(inbound_event)` on the claim query (`delivery.py:53-56`); `test_batch_of_50_uses_one_select` (before_cursor_execute counter) → batch of 50 = **1 claim SELECT** (≤2 allowed), all 50 DELIVERED, PASSED.

---

<a id="FL-4"></a>
## FL-4 [MED] — Ingest re-serializes the body: parse → dump → store

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/routes.py:26,38-41`

**Current code:**
```python
data = request.get_json(silent=True)        # L26 parse #1
if not data:
    return jsonify({"error": "invalid JSON"}), 400
...
if isinstance(payload, dict):
    payload_str = json.dumps(payload)       # L39 re-serialize
else:
    payload_str = str(payload)              # L41 <-- lossy for lists/scalars
```

**Problem:** Double parse/serialize per webhook; `str(payload)` is lossy. Storing the raw bytes is cheaper
and byte-identical.

**Change to:**
```python
raw = request.get_data()                    # read raw bytes once
if not raw:
    return jsonify({"error": "invalid JSON"}), 400
data = json.loads(raw)                      # parse once for validation/event_type
...
payload_str = raw.decode()                  # store the original body, no re-serialize
```

- [x] **FL-4** — apply change; Expected: one parse per webhook, byte-identical payloads. Proof: round-trip test with nested dict/list/scalar payloads asserting stored bytes == received bytes. — **Verified 2026-08-01:** `raw = request.get_data()` read-once + single `json.loads(raw)` + `payload_str = raw.decode()` stored, no re-serialize (`routes.py:36-58`); 6 parametrized byte-identity cases (nested dict, list, scalar int/str, unicode escape, whitespace) — stored `payload.encode() == body` for all, `tests/test_ingest_roundtrip.py` PASSED.

---

<a id="FL-5"></a>
## FL-5 [MED] — Purge deletes unbounded in one transaction on unindexed columns

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/cli.py:35-49` + `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/models.py:31,65`

**Current code:**
```python
# cli.py L35-46 — full-table deletes, no chunking
old_attempts = DeliveryAttempt.query.filter(
    DeliveryAttempt.attempted_at < cutoff          # L36 <-- models.py:65 no index
).delete()
old_outbox = DeliveryOutbox.query.filter(
    DeliveryOutbox.created_at < cutoff,
    DeliveryOutbox.status.in_(["DELIVERED", "DEAD_LETTER"]),
).delete()
old_events = InboundEvent.query.filter(
    InboundEvent.received_at < cutoff              # L45 <-- models.py:31 no index
).delete()
db.session.commit()                                # L48
```
```python
# models.py L31 / L65
received_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))   # L31 no index
attempted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc)) # L65 no index
```

**Problem:** Unindexed filters → full scans on a busy relay; one giant transaction per table.

**Change to** (indexes + chunked deletes):
```python
class InboundEvent(db.Model):
    __table_args__ = (
        db.Index("ix_inbound_idempotency", "idempotency_key"),
        db.Index("ix_inbound_received_at", "received_at"),     # new
    )
```
```python
class DeliveryAttempt(db.Model):
    __table_args__ = (
        db.Index("ix_attempts_outbox", "delivery_outbox_id"),
        db.Index("ix_attempts_attempted_at", "attempted_at"),  # new
    )
```
```python
# cli.py — chunk each delete, e.g. by PK range, ~1000 rows/transaction:
last_id = 0
while True:
    batch = DeliveryAttempt.query.filter(
        DeliveryAttempt.attempted_at < cutoff,
        DeliveryAttempt.id > last_id,
    ).order_by(DeliveryAttempt.id).limit(1000)
    count = batch.delete(synchronize_session=False)
    db.session.commit()
    if count < 1000:
        break
```

- [x] **FL-5** — apply change; Expected: purge no longer full-scans or holds a giant transaction. Proof: `EXPLAIN` on the purge filters; duration measurement on a 1M-row fixture. — **Verified 2026-08-01:** indexes `ix_inbound_received_at` (`models.py:43`), `ix_attempts_attempted_at` (`models.py:97`), `ix_outbox_created_at` (`models.py:74`); `_purge_chunks` (`cli.py:21-33`) = 1000-row PK chunks, SELECT+DELETE+commit per chunk. `EXPLAIN QUERY PLAN` on bench DB: all three purge predicates use covering indexes (no full scans); `test_purge_predicates_use_indexes` PASSED. Bench: `python3 -m bench.maintenance_bench purge` — 100k×3 rows in **4.80s**, rss 171 MB, 0 remaining.

---

<a id="FL-6"></a>
## FL-6 [LOW] — Worker sleeps the full poll interval even when work exists

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/cli.py:19-23`

**Current code:**
```python
while True:
    delivered = worker.run_once()          # L20
    if delivered:
        logger.info("Delivered %d items", delivered)
    time.sleep(poll_interval)              # L23 <-- +5s idle latency even with work queued
```

**Change to:**
```python
while True:
    delivered = worker.run_once()
    if delivered:
        logger.info("Delivered %d items", delivered)
        continue                           # loop immediately while work exists
    time.sleep(poll_interval)              # sleep only when queue was empty
```

- [x] **FL-6** — apply change; Expected: delivery latency under load not inflated by the poll interval. Proof: synthetic queue — second run starts immediately after a non-empty run. — **Verified 2026-08-01:** `_worker_loop` (`cli.py:12-18`) — `continue` after non-empty run, `time.sleep(poll_interval)` only when queue empty; `test_second_run_starts_immediately_after_non_empty_run` (run_once returns 50 then 0 → 2 runs, exactly 1 sleep) PASSED.

---

<a id="FL-7"></a>
## FL-7 [LOW] — Default DB is sqlite; concurrent writes will lock

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/config.py:4`

**Current code:**
```python
SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///relay.db")   # L4
```

**Problem:** A worker doing concurrent writes against sqlite hits `database is locked` (same failure mode as
Django's concurrency test, `02-django-flash-sale-inventory.md#DJ-7`).

**Change to:**
```python
SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "postgresql://postgres@localhost/relay")
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_size": 10,
    "max_overflow": 5,
    "pool_pre_ping": True,
}
```
Keep sqlite only for unit tests (tests/conftest.py).

- [~] **FL-7** — apply change; Expected: no `database is locked` under concurrent worker+ingest. Proof: full pytest green against Postgres; documented command in this row. — **Applied + verified in code 2026-08-01, Postgres validation pending:** env-driven Postgres default + pool options applied (`config.py:6-11`: `SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "postgresql://postgres@localhost/relay")`, `SQLALCHEMY_ENGINE_OPTIONS = {pool_size: 10, max_overflow: 5, pool_pre_ping: True}`); `test_default_database_url_is_postgres` + `test_engine_options_pooling` PASSED; full pytest green on sqlite (`DATABASE_URL=sqlite:///bench_relay.db`, `instance/bench_relay.db`); next gate: Postgres host.

---

<a id="FL-8"></a>
## FL-8 [LOW] — `redrive_dead_letter` loads the whole DLQ into memory

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/flask-partner-webhook-relay/app/cli.py:51-63`

**Current code:**
```python
items = DeliveryOutbox.query.filter_by(status="DEAD_LETTER").all()   # L54 <-- full load
count = 0
for item in items:                                                   # L56
    item.status = "PENDING"
    item.next_attempt_at = datetime.now(timezone.utc)
    item.attempt_count = 0
    item.last_error = None
    count += 1
db.session.commit()                                                  # L62
```

**Change to** (single bulk update):
```python
count = DeliveryOutbox.query.filter_by(status="DEAD_LETTER").update({
    "status": "PENDING",
    "next_attempt_at": datetime.now(timezone.utc),
    "attempt_count": 0,
    "last_error": None,
})
db.session.commit()
print(f"Redrove {count} items")
```

- [x] **FL-8** — apply change; Expected: constant memory regardless of DLQ size. Proof: run on a 100k DLQ fixture; assert RSS flat. — **Verified 2026-08-01:** single bulk `DeliveryOutbox.query.filter_by(status="DEAD_LETTER").update({...})` + one commit (`cli.py:79-88`); `test_redrive_dead_letter_bulk` PASSED; bench `python3 -m bench.maintenance_bench redrive` — 100k DEAD_LETTER rows in **0.46s**, rss **71 MB** flat (no full-model load), dead_letter=0, pending=100000.
