# Phase 1 — Reservation hot path

**Status:** implemented  
**Depends on:** Phase 0  
**Goal:** Specify concurrent reserve/release behavior under sale-open load.

## Checklist — functional requirements

- [x] Create reservation holding quantity across one or more warehouses
- [x] Reject when insufficient stock (no silent partial oversell)
- [x] Release reservation on cancel or TTL expiry
- [x] Confirm reservation for payment (stock remains committed)
- [x] Idempotent reserve when client retries with the same key
- [x] Tenant/store scoping (single-store v1) if multi-store (document if single-store v1)

## Checklist — performance requirements

- [x] Reserve path must use a **bounded** number of queries relative to lines, not warehouses×sku cartesian chat
- [x] Avoid Python-side loops that each hit the DB for stock checks (N+1 warehouse reads)
- [x] Transaction duration must stay short under contention
- [x] Locking strategy must be explicit (`select_for_update` scope, lock ordering to avoid deadlocks)
- [x] Hot path must not recompute heavy derived fields (pricing rules, string templates) per warehouse row
- [x] Logging on success path must not serialize entire reservation graphs at info level

## Checklist — concurrency requirements

- [x] Define isolation expectations for stock decrement
- [x] Define deadlock retry policy (lock ordering prevents) (bounded)
- [x] Define behavior when hold TTL job races with confirm
- [x] Define fairness: one SKU hotspot must not lock unrelated SKUs longer than needed

## Checklist — failure modes

- [x] Sale not open / sale halted
- [ ] Stock races between two shops
- [x] Expired hold confirmed late
- [x] Partial multi-line cart failure policy (all-or-nothing recommended for v1)

## Checklist — acceptance for Phase 1 design

- [x] State machine for reservation statuses written
- [x] Locking / ordering rules written
- [x] Idempotency behavior written
- [ ] “Queries allowed on reserve path” budget written (numeric target)

## Exit criteria

- [x] Hot-path checklist complete
- [x] Ready for Phase 2 (ORM + Postgres)
