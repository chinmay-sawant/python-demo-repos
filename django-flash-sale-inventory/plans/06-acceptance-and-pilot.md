# Phase 6 — Acceptance and pilot gate

**Status:** planning  
**Depends on:** Phases 0–5  
**Goal:** Requirements complete enough to implement or to drive CodeHound rules.

## Checklist — requirements completeness

- [ ] Domain locked
- [ ] Reservation hot path locked
- [ ] ORM + Postgres integrated plan locked
- [ ] Availability read path locked
- [ ] Middleware cost locked
- [ ] Detector themes prioritized

## Checklist — pilot scenarios (when app exists)

- [ ] Sale-open thundering herd without oversell
- [ ] Hot SKU contention remains correct with bounded p99
- [ ] Collection page query count stays flat as SKU count grows (within design)
- [ ] TTL release under load does not deadlock reserve path
- [ ] Worker × DB connection math holds in staging

## Checklist — CodeHound readiness

- [ ] P0 ORM/loop themes mapped to future modules
- [ ] Stay feature-flagged until seed rules exist
- [ ] No marketing claim of Django support until pilot review

## Checklist — open decisions

- [ ] Ledger vs balance-only stock model
- [ ] Cache on availability (yes/no for v1)
- [ ] Multi-store tenancy (yes/no for v1)
- [ ] Partial cart failure policy

## Exit criteria

- [ ] Decisions closed or deferred with reason
- [ ] Plans implementation-ready
