# Phase 6 — Acceptance and pilot gate

**Status:** planning  
**Depends on:** Phases 0–5  
**Goal:** Define when this project’s requirements are “done enough” to implement code (outside this tree) or to drive CodeHound rules.

## Checklist — requirements completeness

- [ ] Phase 0 domain locked
- [ ] Phase 1 ingest contract locked
- [ ] Phase 2 persistence/pool locked (DB integrated here)
- [ ] Phase 3 async + outbound clients locked
- [ ] Phase 4 read path locked
- [ ] Phase 5 detector themes prioritized

## Checklist — pilot scenarios (manual / load, when app exists)

- [ ] Steady ingest for N minutes without memory growth
- [ ] Burst ingest at K× steady without cascade failure
- [ ] Vendor endpoint artificial delay does not fail primary ingest
- [ ] Dashboard percentile queries during burst stay within freshness/latency budget
- [ ] Pool timeout path returns controlled errors, not worker death

## Checklist — CodeHound readiness (meta)

- [ ] At least five P0 detector themes mapped to concrete modules in a future app layout
- [ ] Agreement: stay behind `--features python` until seed rules exist
- [ ] Agreement: do not claim FastAPI support in marketing until pilot findings reviewed

## Checklist — open decisions log

- [ ] Aggregation strategy (raw vs pre-roll) decided
- [ ] Primary HTTP client library decided
- [ ] Partial batch failure policy decided
- [ ] Degraded mode policy decided

## Exit criteria

- [ ] All open decisions closed or explicitly deferred with reason
- [ ] Project plans considered **implementation-ready**
- [ ] Parent corpus README still accurate
