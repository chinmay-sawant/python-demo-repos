## Summary

Implement the full Django flash-sale multi-warehouse inventory reservation system — models, reservation service with concurrency control, availability API, middleware, background TTL expiry, and 25 passing tests.

---

## Motivation / context

- Plans: `django-flash-sale-inventory/plans/`
- Issues: see **Related issues**

---

## Changes

### Models & project scaffolding

- 7 Django models: `SaleEvent`, `Sku`, `Warehouse`, `WarehouseStock`, `Reservation`, `ReservationLine`, `StockLedger`
- Django project config with settings, URL routing, admin registration
- Proper indexes for sale-open access patterns and reservation lookups

### Reservation hot path

- `ReservationService.reserve()` — all-or-nothing multi-warehouse reserve with `select_for_update` lock ordering, idempotency key support, auto-warehouse allocation
- `ReservationService.confirm()` / `cancel()` — status transitions with stock reclamation
- `ReservationService.release_expired()` — batch TTL expiry with `skip_locked` to avoid contention
- Custom exception hierarchy (`InsufficientStockError`, `SaleNotActiveError`, etc.)

### Availability API

- `AvailabilityService` with single-query SKU availability, batch availability, and warehouse rollup
- Three JSON endpoints: `GET /api/skus/{code}/availability/`, `POST /api/availability/batch/`, `GET /api/warehouses/{code}/rollup/`
- Region filtering, proper error handling (400/404/500)

### Middleware & background jobs

- `RequestTimingMiddleware` — monotonic timing, slow-request logging, `X-Request-Duration-Ms` header
- `SaleEventHeaderMiddleware` — parse `X-Sale-Event-Id` header into request
- `expire_reservations` management command with configurable batch size

### Tests

- 25 tests across 6 classes: reservation service (12), concurrency (1), availability service (5), views (5), middleware (2), management command (1)
- Covers success paths, edge cases, idempotency, all-or-nothing semantics, concurrency safety

---

## Impact

| Area | Impact |
|------|--------|
| **Performance** | Bounded queries per request, `select_for_update` with ordered locking prevents deadlocks |
| **Memory** | No large in-memory allocations — queryset aggregation throughout |
| **Behavior / correctness** | All-or-nothing reserves, hard oversell failure, idempotent retries |
| **API / CLI** | 3 new JSON endpoints, 1 management command |
| **Dependencies** | Django 4.2+, psycopg2-binary, python-dotenv |
| **Binary size / build time** | N/A (Python) |

---

## Breaking changes / migration

| Item | Migration |
|------|-----------|
| None | Initial implementation — new project |

---

## Test plan

- [x] `python3 manage.py test inventory` — 25/25 pass

### Commands

```sh
cd django-flash-sale-inventory && python3 manage.py test inventory
```

---

## Screenshots / sample output

```
Found 25 test(s).
...
Ran 25 tests in 0.110s
OK
```

---

## Related issues

- Relates to #1 (project init)

---

## PR metadata checklist (author)

- [x] Self-assigned (`--assignee @me`)
- [x] Labels applied
- [x] Related issues filled with real ticket IDs
- [x] Filled body committed under `PR/pr-django-flash-sale.md`

---

## Follow-ups (out of scope)

- CodeHound detection rule fixtures (Phase 5)
- Full PostgreSQL migration instead of SQLite dev default
- Multi-store tenancy
- Caching layer for availability reads
