# Phase 0 — Domain and boundaries

**Status:** implemented  
**Project:** `django-flash-sale-inventory`  
**Goal:** Lock a flash-sale inventory domain that forces ORM and concurrency performance work.

## Checklist — domain lock

- [x] Confirm product name in docs: **Flash-sale multi-warehouse inventory reservation**
- [x] Confirm sale window concept (fixed start/end; thundering herd at open)
- [x] Confirm stock is split across **warehouses** with region affinity
- [x] Confirm shopper action: **reserve** (hold) then later confirm/cancel
- [x] Confirm merchandising action: live **availability aggregates** during sale
- [x] Confirm oversell is a **hard failure** (correctness under load, not eventual “sorry”)

## Checklist — actors

- [x] Shopper (PDP + checkout reserve)
- [x] Sale edge / CDN (read-heavy availability widgets)
- [x] Merchandiser / ops (warehouse rollups, emergency halt)
- [x] Payment step (external; only consumes reservation id — not full PSP scope)

## Checklist — load shape

- [x] Define expected concurrent reserving users at sale open
- [x] Define SKU count in a typical drop
- [x] Define warehouse count (small but >1 — multi-row allocation)
- [x] Define hold TTL before auto-release (300s default)
- [x] Define read/write ratio during sale (reads dominate; writes spike)

## Checklist — data concepts (names only)

- [x] `SaleEvent`
- [x] `Sku`
- [x] `Warehouse`
- [x] `WarehouseStock`
- [x] `Reservation` (hold with expiry)
- [x] `ReservationLine` (sku + warehouse + qty)
- [x] `StockLedger` (optional audit of mutations)

## Checklist — non-goals

- [x] Full WMS / picking workflows
- [x] Multi-primary multi-region inventory v1
- [x] Complex promotions engine
- [x] Storefront UI beyond API/view contracts
- [x] Code snippets in plan files

## Exit criteria

- [x] Problem statement agreed
- [x] Load shape agreed
- [x] Non-goals agreed
- [x] Ready for Phase 1
