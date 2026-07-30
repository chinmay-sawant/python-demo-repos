# Phase 1 — Reservation hot path

**Status:** planning  
**Depends on:** Phase 0  
**Goal:** Specify concurrent reserve/release behavior under sale-open load.

## Checklist — functional requirements

- [ ] Create reservation holding quantity across one or more warehouses
- [ ] Reject when insufficient stock (no silent partial oversell)
- [ ] Release reservation on cancel or TTL expiry
- [ ] Confirm reservation for payment (stock remains committed)
- [ ] Idempotent reserve when client retries with the same key
- [ ] Tenant/store scoping if multi-store (document if single-store v1)

## Checklist — performance requirements

- [ ] Reserve path must use a **bounded** number of queries relative to lines, not warehouses×sku cartesian chat
- [ ] Avoid Python-side loops that each hit the DB for stock checks (N+1 warehouse reads)
- [ ] Transaction duration must stay short under contention
- [ ] Locking strategy must be explicit (`select_for_update` scope, lock ordering to avoid deadlocks)
- [ ] Hot path must not recompute heavy derived fields (pricing rules, string templates) per warehouse row
- [ ] Logging on success path must not serialize entire reservation graphs at info level

## Checklist — concurrency requirements

- [ ] Define isolation expectations for stock decrement
- [ ] Define deadlock retry policy (bounded)
- [ ] Define behavior when hold TTL job races with confirm
- [ ] Define fairness: one SKU hotspot must not lock unrelated SKUs longer than needed

## Checklist — failure modes

- [ ] Sale not open / sale halted
- [ ] Stock races between two shops
- [ ] Expired hold confirmed late
- [ ] Partial multi-line cart failure policy (all-or-nothing recommended for v1)

## Checklist — acceptance for Phase 1 design

- [ ] State machine for reservation statuses written
- [ ] Locking / ordering rules written
- [ ] Idempotency behavior written
- [ ] “Queries allowed on reserve path” budget written (numeric target)

## Exit criteria

- [ ] Hot-path checklist complete
- [ ] Ready for Phase 2 (ORM + Postgres)
