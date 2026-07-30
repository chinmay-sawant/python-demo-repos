# Phase 3 — Retry loops and payload cost

**Status:** planning  
**Depends on:** Phase 2  
**Goal:** Keep retries correct without CPU/memory thrash on hot failure paths.

## Checklist — retry requirements

- [ ] Exponential backoff with jitter
- [ ] Max attempts and max retention age
- [ ] Distinguish retryable vs permanent failures
- [ ] Retry must re-sign if timestamp-based signatures require it
- [ ] Retry storm prevention per partner

## Checklist — performance requirements on retry path

- [ ] Avoid rebuilding huge diagnostic strings every attempt at info log level
- [ ] Avoid repeated JSON dumps of identical payload without need
- [ ] Avoid regex recompile for signature canonicalization inside attempt loops
- [ ] Temporary payload staging files (if any) must use clear lifecycle (no leak per attempt)
- [ ] Sleep/backoff must not hold DB transactions open

## Checklist — correctness

- [ ] Idempotency keys stable across retries
- [ ] Attempt numbering monotonic
- [ ] Dead-letter path after exhaustion

## Checklist — acceptance

- [ ] Retry state machine written
- [ ] Cost rules for serialization/logging written
- [ ] Dead-letter policy written

## Exit criteria

- [ ] Ready for Phase 4 (persistence integrated)
