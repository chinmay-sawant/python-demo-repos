# django-flash-sale-inventory

**Stack:** Django · Django ORM · PostgreSQL · cache (optional later)  
**Plans only:** see [`plans/`](./plans/)

## Domain (specific, performance-sensitive)

Build a **flash-sale inventory reservation** backend for a multi-warehouse retail
site.

During limited drops (e.g. 10-minute sale windows):

- Shoppers reserve SKUs across **multiple warehouses**.
- The system must allocate stock without oversell, show live remaining units,
  and confirm holds before payment.
- Merchandising tools need **aggregated availability** by region and warehouse
  during the sale.

This is **not** a generic employee directory. The hard problem is **request-path
ORM cost** under concurrent reservation: query-in-loop warehouse checks,
missing `select_related` / `prefetch_related`, chatty stock updates, and
transaction boundaries that serialize the whole site.

## Primary performance SLOs (planning targets)

| Path | Target direction |
|------|------------------|
| Reserve | High concurrency; no oversell; predictable p99 under thundering herd |
| Availability read | Listing and PDP stock widgets stay cheap during sale |
| Admin aggregate | Warehouse rollups must not full-table-scan on every refresh |
| DB | Connection use and per-request query count are first-class metrics |

## Technologies in scope (integrated, not separate folders)

- Django request/response and middleware cost on hot paths
- Django ORM (N+1, deferred fields, bulk ops, `select_for_update`)
- PostgreSQL as the system of record for stock and reservations
- Optional cache layer only as a later phase concern (not a separate project)

## Explicit non-goals

- Full payments PSP integration depth
- Warehouse robotics / WMS replacement
- Multi-primary distributed inventory v1
- Code samples in these plans

## Plan index

| Phase | File | Theme |
|-------|------|--------|
| 0 | [`plans/00-domain-and-boundaries.md`](./plans/00-domain-and-boundaries.md) | Domain lock, actors, non-goals |
| 1 | [`plans/01-reservation-hot-path.md`](./plans/01-reservation-hot-path.md) | Concurrent reserve / release |
| 2 | [`plans/02-orm-and-postgres.md`](./plans/02-orm-and-postgres.md) | ORM patterns, locking, batching |
| 3 | [`plans/03-availability-and-aggregates.md`](./plans/03-availability-and-aggregates.md) | Listing and rollup read paths |
| 4 | [`plans/04-middleware-and-request-cost.md`](./plans/04-middleware-and-request-cost.md) | Per-request work outside views |
| 5 | [`plans/05-codehound-detection-targets.md`](./plans/05-codehound-detection-targets.md) | What CodeHound should eventually flag |
| 6 | [`plans/06-acceptance-and-pilot.md`](./plans/06-acceptance-and-pilot.md) | Pilot checklist before implementation |
