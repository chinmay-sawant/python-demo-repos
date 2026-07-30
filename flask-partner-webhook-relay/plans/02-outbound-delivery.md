# Phase 2 — Outbound delivery (sync HTTP)

**Status:** implemented  
**Depends on:** Phase 1  
**Goal:** Specify high-volume partner delivery with isolation, timeouts, and connection reuse.

## Checklist — functional requirements

- [x] Sign payloads per partner secret
- [x] POST to partner endpoint with required headers
- [x] Record attempt status, latency, response code
- [x] Honor partner disable / circuit conditions
- [x] Support manual redrive for ops

## Checklist — performance requirements

- [x] Every HTTP call has connect and read **timeouts**
- [x] Use a **shared session** / connection pool across attempts in a worker
- [x] Bound concurrency per worker and per partner
- [x] Partner slowness must not block unrelated partners forever
- [x] DNS / TLS setup cost amortized via reuse
- [x] No new TCP client session per attempt on the happy path

## Checklist — isolation requirements

- [x] Per-partner concurrency caps
- [x] Circuit breaker or temporary quarantine after error threshold
- [x] Fair scheduling so one large partner cannot starve others

## Checklist — anti-patterns to design against

- [x] Missing timeouts (`requests` defaults that hang)
- [x] `requests.get/post` without session reuse in a tight loop
- [x] Sequential fan-out that makes total latency sum of all partners inside one job incorrectly scoped
- [x] Loading all pending rows into memory each poll

## Checklist — acceptance

- [x] Timeout numbers proposed
- [x] Pool / session lifecycle rules written
- [x] Concurrency caps written
- [x] Circuit policy written

## Exit criteria

- [x] Ready for Phase 3 (retry + payload cost)
