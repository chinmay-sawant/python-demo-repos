# Phase 6 — Acceptance and pilot gate

**Status:** planning  
**Depends on:** Phases 0–5  
**Goal:** Requirements complete enough to implement or to drive CodeHound rules.

## Checklist — requirements completeness

- [ ] Domain locked
- [ ] Ingest/queue locked
- [ ] Outbound delivery locked
- [ ] Retry/payload cost locked
- [ ] Persistence integrated locked
- [ ] Detector themes prioritized

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

- [ ] DB outbox vs external queue for v1
- [ ] Attempt log sync vs batched
- [ ] Primary HTTP library (`requests` vs alternatives)
- [ ] Circuit breaker thresholds

## Exit criteria

- [ ] Decisions closed or deferred with reason
- [ ] Plans implementation-ready
