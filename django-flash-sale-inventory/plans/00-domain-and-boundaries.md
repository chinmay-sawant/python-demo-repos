# Phase 0 — Domain and boundaries

**Status:** planning  
**Project:** `django-flash-sale-inventory`  
**Goal:** Lock a flash-sale inventory domain that forces ORM and concurrency performance work.

## Checklist — domain lock

- [ ] Confirm product name in docs: **Flash-sale multi-warehouse inventory reservation**
- [ ] Confirm sale window concept (fixed start/end; thundering herd at open)
- [ ] Confirm stock is split across **warehouses** with region affinity
- [ ] Confirm shopper action: **reserve** (hold) then later confirm/cancel
- [ ] Confirm merchandising action: live **availability aggregates** during sale
- [ ] Confirm oversell is a **hard failure** (correctness under load, not eventual “sorry”)

## Checklist — actors

- [ ] Shopper (PDP + checkout reserve)
- [ ] Sale edge / CDN (read-heavy availability widgets)
- [ ] Merchandiser / ops (warehouse rollups, emergency halt)
- [ ] Payment step (external; only consumes reservation id — not full PSP scope)

## Checklist — load shape

- [ ] Define expected concurrent reserving users at sale open
- [ ] Define SKU count in a typical drop
- [ ] Define warehouse count (small but >1 — multi-row allocation)
- [ ] Define hold TTL before auto-release
- [ ] Define read/write ratio during sale (reads dominate; writes spike)

## Checklist — data concepts (names only)

- [ ] `SaleEvent`
- [ ] `Sku`
- [ ] `Warehouse`
- [ ] `WarehouseStock`
- [ ] `Reservation` (hold with expiry)
- [ ] `ReservationLine` (sku + warehouse + qty)
- [ ] `StockLedger` (optional audit of mutations)

## Checklist — non-goals

- [ ] Full WMS / picking workflows
- [ ] Multi-primary multi-region inventory v1
- [ ] Complex promotions engine
- [ ] Storefront UI beyond API/view contracts
- [ ] Code snippets in plan files

## Exit criteria

- [ ] Problem statement agreed
- [ ] Load shape agreed
- [ ] Non-goals agreed
- [ ] Ready for Phase 1
