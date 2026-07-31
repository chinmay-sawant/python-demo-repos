# Phase 3 — flask-partner-webhook-relay (Evaluation + Improvements)

> **Canonical ledger:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/plans/perf-evaluation/README.md`
> **Status:** Evaluation complete; improvements not started
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

- [ ] **FL-1** — apply change; Expected: fan-out wall-clock ≈ slowest partner, not Σ partners; the never-used config keys (config.py:13, models.py:22) take effect. Proof: benchmark — 50 outbox items to a 200ms mock endpoint: total ≤ ~1s (was ~10s+); unit test asserting per-endpoint concurrency ≤ cap. **Requires FL-2 first (atomic claim).**

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

- [ ] **FL-2** — apply change; Expected: exactly-one claim per row across concurrent workers. Proof: two concurrent workers over a 200-row queue claim disjoint sets (union == 200, no overlap).

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

- [ ] **FL-3** — apply change; Expected: batch of 50 → ~1 query instead of 100+. Proof: assert query count in a worker test via SQLAlchemy event logging.

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

- [ ] **FL-4** — apply change; Expected: one parse per webhook, byte-identical payloads. Proof: round-trip test with nested dict/list/scalar payloads asserting stored bytes == received bytes.

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

- [ ] **FL-5** — apply change; Expected: purge no longer full-scans or holds a giant transaction. Proof: `EXPLAIN` on the purge filters; duration measurement on a 1M-row fixture.

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

- [ ] **FL-6** — apply change; Expected: delivery latency under load not inflated by the poll interval. Proof: synthetic queue — second run starts immediately after a non-empty run.

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

- [ ] **FL-7** — apply change; Expected: no `database is locked` under concurrent worker+ingest. Proof: full pytest green against Postgres; documented command in this row.

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

- [ ] **FL-8** — apply change; Expected: constant memory regardless of DLQ size. Proof: run on a 100k DLQ fixture; assert RSS flat.
