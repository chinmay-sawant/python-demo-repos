# Phase 2 — Outbound delivery (sync HTTP)

**Status:** planning  
**Depends on:** Phase 1  
**Goal:** Specify high-volume partner delivery with isolation, timeouts, and connection reuse.

## Checklist — functional requirements

- [ ] Sign payloads per partner secret
- [ ] POST to partner endpoint with required headers
- [ ] Record attempt status, latency, response code
- [ ] Honor partner disable / circuit conditions
- [ ] Support manual redrive for ops

## Checklist — performance requirements

- [ ] Every HTTP call has connect and read **timeouts**
- [ ] Use a **shared session** / connection pool across attempts in a worker
- [ ] Bound concurrency per worker and per partner
- [ ] Partner slowness must not block unrelated partners forever
- [ ] DNS / TLS setup cost amortized via reuse
- [ ] No new TCP client session per attempt on the happy path

## Checklist — isolation requirements

- [ ] Per-partner concurrency caps
- [ ] Circuit breaker or temporary quarantine after error threshold
- [ ] Fair scheduling so one large partner cannot starve others

## Checklist — anti-patterns to design against

- [ ] Missing timeouts (`requests` defaults that hang)
- [ ] `requests.get/post` without session reuse in a tight loop
- [ ] Sequential fan-out that makes total latency sum of all partners inside one job incorrectly scoped
- [ ] Loading all pending rows into memory each poll

## Checklist — acceptance

- [ ] Timeout numbers proposed
- [ ] Pool / session lifecycle rules written
- [ ] Concurrency caps written
- [ ] Circuit policy written

## Exit criteria

- [ ] Ready for Phase 3 (retry + payload cost)
