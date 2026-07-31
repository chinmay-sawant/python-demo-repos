# Phase 2 — django-flash-sale-inventory (Evaluation + Improvements)

> **Canonical ledger:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/plans/perf-evaluation/README.md`
> **Status:** Evaluation complete; improvements not started
> **Project root:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory`
> **Baseline:** `python3 manage.py test` → 24 passed, **1 FAILED** — `test_concurrent_reserve_no_deadlock`
> (`inventory/tests.py:399`, sqlite `database table is locked`)

Hot paths: `reserve()` (N items, multi-warehouse, `select_for_update`), `confirm`/`cancel`, `release_expired` sweep, availability read APIs.

---

<a id="DJ-1"></a>
## DJ-1 [HIGH] — Reserve does N+1 lookups: `Sku.get` / `Warehouse.get` inside the item loop

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/services/reservation.py:36-57` (hot lines `:38`, `:40`)

**Current code:**
```python
item_details = []
for item in items:                                              # L37
    sku = Sku.objects.get(sku_code=item['sku_code'])            # L38 <-- 1 query per item
    if warehouse_code:
        warehouse = Warehouse.objects.get(code=warehouse_code)  # L40 <-- same query repeated N times
        stock_qs = WarehouseStock.objects.filter(warehouse=warehouse, sku=sku)
    else:
        stock_qs = WarehouseStock.objects.filter(sku=sku).select_related('warehouse').order_by('-quantity')  # L43
    stock = stock_qs.select_for_update().first()                # L45
    if not stock or stock.available_quantity() < item['quantity']:  # L46
        raise InsufficientStockError(...)
    item_details.append({'sku': sku, 'stock': stock, 'quantity': item['quantity']})  # L53-57
```

**Problem:** 2N extra queries per order; the `Warehouse` lookup repeats the identical query for every item.

**Change to:**
```python
skus = {s.sku_code: s for s in Sku.objects.filter(sku_code__in=[i['sku_code'] for i in items])}
warehouse = Warehouse.objects.get(code=warehouse_code) if warehouse_code else None

item_details = []
for item in items:
    sku = skus[item['sku_code']]
    stock_qs = (
        WarehouseStock.objects.filter(warehouse=warehouse, sku=sku)
        if warehouse
        else WarehouseStock.objects.filter(sku=sku).select_related('warehouse').order_by('-quantity')
    )
    stock = stock_qs.select_for_update().first()                # L45 unchanged
    if not stock or stock.available_quantity() < item['quantity']:
        raise InsufficientStockError(...)
    item_details.append({'sku': sku, 'stock': stock, 'quantity': item['quantity']})
```

- [ ] **DJ-1** — apply change; Expected: 2N queries → ~2. Proof: query-count assertion via `django.db.connection.queries` in tests (`inventory/tests.py`); `python3 manage.py test` still 24/25 (same single failure until DJ-7).

---

<a id="DJ-2"></a>
## DJ-2 [HIGH] — Reserve does 3N writes per order (create per line, update per line, ledger per line)

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/services/reservation.py:67-82` (hot lines `:68`, `:74`, `:77`)

**Current code:**
```python
for detail in item_details:
    ReservationLine.objects.create(                             # L68 1 query per line
        reservation=reservation,
        sku=detail['sku'],
        warehouse=detail['stock'].warehouse,
        quantity=detail['quantity'],
    )
    WarehouseStock.objects.filter(pk=detail['stock'].pk).update(  # L74 already atomic via F()
        reserved_quantity=models.F('reserved_quantity') + detail['quantity']
    )
    StockLedger.objects.create(                                 # L77 1 query per line
        warehouse_stock=detail['stock'],
        delta=-detail['quantity'],
        reason=StockLedger.Reason.RESERVE,
        reservation=reservation,
    )
```

**Problem:** 3N round-trips per order; flash-sale checkout throughput is round-trip-bound.

**Change to:**
```python
ReservationLine.objects.bulk_create([
    ReservationLine(
        reservation=reservation,
        sku=d['sku'],
        warehouse=d['stock'].warehouse,
        quantity=d['quantity'],
    )
    for d in item_details
])
for d in item_details:                                          # 1 query per stock row (keeps F() atomicity)
    WarehouseStock.objects.filter(pk=d['stock'].pk).update(
        reserved_quantity=models.F('reserved_quantity') + d['quantity']
    )
StockLedger.objects.bulk_create([
    StockLedger(
        warehouse_stock=d['stock'],
        delta=-d['quantity'],
        reason=StockLedger.Reason.RESERVE,
        reservation=reservation,
    )
    for d in item_details
])
```

- [ ] **DJ-2** — apply change; Expected: 3N writes → ≤ N+2. Proof: query-count test; existing reserve/confirm/cancel tests pass.

---

<a id="DJ-3"></a>
## DJ-3 [HIGH] — confirm/cancel/expire use read-modify-write `.save()` → lost updates + N+1

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/services/reservation.py:98-108` (confirm), `:119-128` (cancel), `:145-154` (expire) — hot lines `:99-102`, `:120-122`, `:146-148`

**Current code (confirm shown; cancel/expire identical pattern):**
```python
for line in reservation.lines.select_related('sku', 'warehouse').all():  # L98
    stock = WarehouseStock.objects.get(warehouse=line.warehouse, sku=line.sku)  # L99 1 query per line
    stock.reserved_quantity -= line.quantity                          # L100 read-modify-write
    stock.quantity -= line.quantity                                   # L101
    stock.save()                                                      # L102 <-- lost-update window
    StockLedger.objects.create(                                       # L103
        warehouse_stock=stock, delta=-line.quantity,
        reason=StockLedger.Reason.CONFIRM, reservation=reservation,
    )
```

**Problem:** Two concurrent confirms/cancels can overwrite each other's deltas (the `version` column at
`models.py:55` exists but is never enforced); each line also costs 2 queries.

**Change to** (atomic `F()` updates + one prefetch):
```python
lines = list(reservation.lines.select_related('sku', 'warehouse').all())  # L98
stocks = {
    (ws.warehouse_id, ws.sku_id): ws
    for ws in WarehouseStock.objects.filter(
        warehouse_id__in=[l.warehouse_id for l in lines],
        sku_id__in=[l.sku_id for l in lines],
    ).select_for_update()
}
for line in lines:
    WarehouseStock.objects.filter(warehouse=line.warehouse, sku=line.sku).update(
        reserved_quantity=models.F('reserved_quantity') - line.quantity,
        quantity=models.F('quantity') - line.quantity,
    )
    StockLedger.objects.create(
        warehouse_stock=stocks[(line.warehouse_id, line.sku_id)],
        delta=-line.quantity,
        reason=StockLedger.Reason.CONFIRM,
        reservation=reservation,
    )
```
Apply the same pattern to `cancel` (L119-128, delta `+line.quantity`, no `quantity` change) and
`release_expired` (L145-154, delta `+line.quantity`).

- [ ] **DJ-3** — apply change; Expected: no lost updates under concurrency; fewer round-trips. Proof: two parallel confirms on the same stock → final stock == expected; `python3 manage.py test` green (the sqlite lock failure closes via DJ-7, not here).

---

<a id="DJ-4"></a>
## DJ-4 [MED] — `release_expired` sweeps the whole expired set in one unbounded transaction

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/services/reservation.py:132-158`

**Current code:**
```python
@transaction.atomic
def release_expired(self):
    cutoff = timezone.now()
    expired_qs = Reservation.objects.filter(                 # L135
        status=Reservation.Status.PENDING,
        expires_at__lt=cutoff,
    ).select_for_update(skip_locked=True)                    # L138
    count = 0
    for reservation in expired_qs:                           # L141 <-- unbounded single transaction
        reservation.status = Reservation.Status.EXPIRED
        reservation.save()
        for line in reservation.lines.select_related('sku', 'warehouse').all():  # L145 (see DJ-3)
            ...
        count += 1
    return count
```

**Problem:** On flash-sale scale one sweep holds locks on all expired rows for the whole run, starving `reserve`.

**Change to** (batch per transaction):
```python
def release_expired(self, batch_size: int = 500):
    cutoff = timezone.now()
    count = 0
    while True:
        with transaction.atomic():
            expired_qs = Reservation.objects.filter(
                status=Reservation.Status.PENDING,
                expires_at__lt=cutoff,
            ).select_for_update(skip_locked=True)[:batch_size]   # bounded batch
            processed = 0
            for reservation in expired_qs:
                reservation.status = Reservation.Status.EXPIRED
                reservation.save()
                # ... per-line F() updates + ledger (see DJ-3) ...
                processed += 1
            count += processed
        if processed < batch_size:                               # no more work
            break
    return count
```

- [ ] **DJ-4** — apply change; Expected: lock hold time bounded; `reserve` no longer starves. Proof: instrument lock wait time under concurrent reserve+expire; management command (`inventory/management/commands/expire_reservations.py:9-11`) still reports the correct count.

---

<a id="DJ-5"></a>
## DJ-5 [MED] — Idempotency race: concurrent duplicate keys raise IntegrityError → 500

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/services/reservation.py:31-34` (create at `:59-65`)

**Current code:**
```python
if idempotency_key:
    existing = Reservation.objects.filter(idempotency_key=idempotency_key).first()  # L32
    if existing:
        return existing
...
reservation = Reservation.objects.create(                        # L59 <-- unique constraint (models.py:80)
    user_id=user_id, sale_event=sale_event, status=Reservation.Status.PENDING,
    idempotency_key=idempotency_key, expires_at=timezone.now() + timedelta(minutes=30),
)
```

**Problem:** Two concurrent requests with the same key both miss the SELECT; the loser hits the unique
constraint and raises IntegrityError → 500 instead of returning the existing reservation.

**Change to:**
```python
if idempotency_key:
    existing = Reservation.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing

try:
    reservation = Reservation.objects.create(
        user_id=user_id, sale_event=sale_event, status=Reservation.Status.PENDING,
        idempotency_key=idempotency_key, expires_at=timezone.now() + timedelta(minutes=30),
    )
except IntegrityError:
    transaction.set_rollback(True)
    return Reservation.objects.get(idempotency_key=idempotency_key)
```

- [ ] **DJ-5** — apply change; Expected: concurrent duplicate keys return the existing reservation, never 500. Proof: new test firing the same key from 2 threads.

---

<a id="DJ-6"></a>
## DJ-6 [MED] — Unbounded request body + ORM hydration on availability reads

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/views.py:23` + `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/services/availability.py:35-47`

**Current code:**
```python
# views.py L23
data = json.loads(request.body)            # <-- no size cap; full body always read+parsed
```
```python
# availability.py L35-47
def get_warehouse_rollup(self, warehouse_code):
    qs = WarehouseStock.objects.filter(
        warehouse__code=warehouse_code,
    ).select_related('sku', 'warehouse')   # L36-38
    result = []
    for ws in qs:                          # L40 <-- instantiates an ORM object per row
        result.append({
            'sku_code': ws.sku.sku_code,
            'quantity': ws.quantity,
            'reserved_quantity': ws.reserved_quantity,
            'available': ws.available_quantity(),
        })
    return result
```

**Problem:** `batch_availability` can be fed an arbitrarily large body (memory blow-up per request);
`get_warehouse_rollup` pays ORM hydration for rows it could project in SQL.

**Change to:**
```python
# views.py: cap before parsing
if len(request.body) > 256 * 1024:
    return JsonResponse({'error': 'payload too large'}, status=413)
data = json.loads(request.body)
```
```python
# availability.py: pure SQL projection
def get_warehouse_rollup(self, warehouse_code):
    return list(
        WarehouseStock.objects.filter(warehouse__code=warehouse_code)
        .values('quantity', 'reserved_quantity')
        .annotate(
            sku_code=models.F('sku__sku_code'),
            available=models.F('quantity') - models.F('reserved_quantity'),
        )
        .order_by('sku_code')
    )
```
(import `from django.db import models` — or `F` directly.)

- [ ] **DJ-6** — apply change; Expected: bounded memory per request; no ORM hydration on read path. Proof: body-limit unit test; assert `QuerySet.values` used (no model hydration) via query log.

---

<a id="DJ-7"></a>
## DJ-7 [LOW] — Default dev DB is sqlite with `DEBUG=True`; concurrency test already fails

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/flash_sale/settings.py:8,54-71` (test failure: `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/tests.py:399`)

**Current code:**
```python
DEBUG = True                                          # L8 <-- prod hazard: query logging, no template caching

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',       # L56 <-- file lock = "database table is locked"
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# DATABASES = {                                        # L61-71 commented Postgres block
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': os.getenv('DB_NAME', 'flash_sale'),
#         ...
#         'CONN_MAX_AGE': 60,
#     }
# }
```

**Problem:** sqlite serializes writers; the concurrent reservation test fails at `tests.py:399` with
`OperationalError: database table is locked`. `DEBUG=True` also disables template/query caching in prod.

**Change to:**
```python
DEBUG = os.getenv('DJANGO_DEBUG', '0') == '1'         # L8

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',   # L56
        'NAME': os.getenv('DB_NAME', 'flash_sale'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': 60,                          # L69 connection reuse
        'CONN_HEALTH_CHECKS': True,
    }
}
```

- [ ] **DJ-7** — apply change; Expected: 25/25 tests pass on Postgres (row locks instead of sqlite file lock); connection reuse cuts handshake cost. Proof: `python3 manage.py test` green against local Postgres; record the DB setup command + dataset in this row.

---

<a id="DJ-8"></a>
## DJ-8 [LOW] — Multi-warehouse stock pick sorts `-quantity` with no supporting index

**Location:** `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/services/reservation.py:43` + model `/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/django-flash-sale-inventory/inventory/models.py:50-65`

**Current code:**
```python
# reservation.py L43
stock_qs = WarehouseStock.objects.filter(sku=sku).select_related('warehouse').order_by('-quantity')
```
```python
# models.py L57-59
class Meta:
    unique_together = [('warehouse', 'sku')]    # index covers single-warehouse branch only
```

**Problem:** The multi-warehouse branch orders by `-quantity`; Postgres must sort unless a `(sku, -quantity)` index exists.

**Change to:**
```python
class Meta:
    unique_together = [('warehouse', 'sku')]
    indexes = [
        models.Index(fields=['sku', '-quantity']),   # new
    ]
```

- [ ] **DJ-8** — apply change; Expected: no sort step on the order-by-quantity branch. Proof: `EXPLAIN` shows Index Scan w/o Sort for the multi-warehouse filter.
