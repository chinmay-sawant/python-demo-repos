# Phase 6 — Acceptance and pilot gate

**Status:** implemented  
**Depends on:** Phases 0–5  
**Goal:** Requirements complete enough to implement or to drive CodeHound rules.

## Checklist — requirements completeness

- [x] Domain locked
- [x] Reservation hot path locked
- [x] ORM + Postgres integrated plan locked
- [x] Availability read path locked
- [x] Middleware cost locked
- [x] Detector themes prioritized

## Checklist — pilot scenarios (when app exists)

- [ ] Sale-open thundering herd without oversell
- [ ] Hot SKU contention remains correct with bounded p99
- [ ] Collection page query count stays flat as SKU count grows (within design)
- [ ] TTL release under load does not deadlock reserve path
- [ ] Worker × DB connection math holds in staging

## Checklist — CodeHound readiness

- [x] P0 ORM/loop themes mapped to future modules
- [x] Stay feature-flagged until seed rules exist
- [x] No marketing claim of Django support until pilot review

## Checklist — open decisions

- [x] Ledger vs balance-only (Ledger chosen, StockLedger created) stock model
- [x] Cache on availability (No for v1) (yes/no for v1)
- [x] Multi-store tenancy (No for v1) (yes/no for v1)
- [x] Partial cart failure policy (all-or-nothing)

## Exit criteria

- [x] Decisions closed or deferred with reason with reason
- [x] Plans implementation-ready
