# Phase 3 — Retry loops and payload cost

**Status:** implemented  
**Depends on:** Phase 2  
**Goal:** Keep retries correct without CPU/memory thrash on hot failure paths.

## Checklist — retry requirements

- [x] Exponential backoff with jitter
- [x] Max attempts and max retention age
- [x] Distinguish retryable vs permanent failures
- [x] Retry must re-sign if timestamp-based signatures require it
- [x] Retry storm prevention per partner

## Checklist — performance requirements on retry path

- [x] Avoid rebuilding huge diagnostic strings every attempt at info log level
- [x] Avoid repeated JSON dumps of identical payload without need
- [x] Avoid regex recompile for signature canonicalization inside attempt loops
- [x] Temporary payload staging files (if any) must use clear lifecycle (no leak per attempt)
- [x] Sleep/backoff must not hold DB transactions open

## Checklist — correctness

- [x] Idempotency keys stable across retries
- [x] Attempt numbering monotonic
- [x] Dead-letter path after exhaustion

## Checklist — acceptance

- [x] Retry state machine written
- [x] Cost rules for serialization/logging written
- [x] Dead-letter policy written

## Exit criteria

- [x] Ready for Phase 4 (persistence integrated)
