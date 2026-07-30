# Phase 6 — Acceptance and pilot gate

**Status:** implemented  
**Depends on:** Phases 0–5  
**Goal:** Requirements complete enough to implement or to drive CodeHound rules.

## Checklist — requirements completeness

- [x] Domain locked
- [x] Ingest/queue locked
- [x] Outbound delivery locked
- [x] Retry/payload cost locked
- [x] Persistence integrated locked
- [x] Detector themes prioritized

## Checklist — pilot scenarios (when app exists)

- [ ] Ingest burst with slow partners: upstream still ACKs quickly
- [ ] One partner timeout does not stall all deliveries
- [ ] Connection reuse visible under load (ops metric)
- [ ] Retry backoff prevents stampedes
- [ ] Queue depth queries remain cheap

## Checklist — CodeHound readiness

- [ ] P0 HTTP timeout/session themes mapped
- [ ] Stay behind feature flag until seed rules exist
- [ ] No marketing claim of Flask support until pilot review

## Checklist — open decisions

- [x] DB outbox vs external (DB outbox chosen) queue for v1
- [x] Attempt log sync vs (sync) batched
- [x] Primary HTTP library (requests) (`requests` vs alternatives)
- [x] Circuit breaker thresholds (config-based)

## Exit criteria

- [x] Decisions closed or deferred with reason with reason
- [x] Plans implementation-ready
