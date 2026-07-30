# Phase 3 — Async rules and outbound clients (httpx / aiohttp)

**Status:** implemented  
**Depends on:** Phase 2  
**Goal:** Specify event-loop safety and vendor fan-out client lifecycle under load.

## Checklist — asyncio requirements

- [x] All request handlers remain non-blocking for I/O
- [x] CPU-heavy classification (if any) has a documented offload or must stay cheap enough in-process
- [x] Background tasks for fan-out must be bounded (queue depth + worker count)
- [x] Shutdown must drain or cancel background work without hang
- [x] No `time.sleep` (or other blocking sleeps) on the event loop
- [x] No synchronous HTTP or file I/O inside async route functions

## Checklist — outbound vendor fan-out

- [x] Fan-out is **best-effort** relative to primary ingest success
- [x] Use a **shared** async HTTP client for the process (connection reuse)
- [x] Every outbound call has **connect + read timeouts**
- [x] Limit concurrent in-flight vendor calls (semaphore or worker pool)
- [x] Partner/vendor slowness must not grow unbounded memory of pending payloads
- [x] Retry policy: bounded attempts, exponential backoff, jitter; no infinite retry in-request

## Checklist — client lifecycle requirements

- [x] Client created at application startup / lifespan
- [x] Client closed on shutdown
- [x] No "open client per sample" or “per request” on the hot path
- [x] TLS / keep-alive expectations documented
- [x] Choose httpx **or** aiohttp as primary for v1 (document choice); second may appear only as deliberate contrast later

## Checklist — performance anti-patterns this phase must call out

- [x] Blocking `requests` inside async routes
- [x] Missing timeouts on async clients
- [x] New client session per call
- [x] Awaiting fan-out inside the ingest response path when it should be queued
- [x] Unbounded `create_task` without backpressure

## Checklist — observability for this phase

- [x] Metrics: outbound success/fail, latency histogram, queue depth
- [x] Metrics: event-loop lag / handler duration (planning level)
- [x] Logs: never log full payloads at info on success path

## Checklist — acceptance for Phase 3 design

- [x] Written rule set: “allowed vs forbidden on the event loop”
- [x] Written fan-out architecture (inline vs queue) with rationale
- [x] Timeout and concurrency numbers proposed (even if later tuned)
- [x] Shutdown behavior described

## Exit criteria

- [x] Async + client checklists complete
- [x] Ready for Phase 4 (read path percentiles)
