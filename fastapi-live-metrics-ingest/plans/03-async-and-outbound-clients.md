# Phase 3 — Async rules and outbound clients (httpx / aiohttp)

**Status:** planning  
**Depends on:** Phase 2  
**Goal:** Specify event-loop safety and vendor fan-out client lifecycle under load.

## Checklist — asyncio requirements

- [ ] All request handlers remain non-blocking for I/O
- [ ] CPU-heavy classification (if any) has a documented offload or must stay cheap enough in-process
- [ ] Background tasks for fan-out must be bounded (queue depth + worker count)
- [ ] Shutdown must drain or cancel background work without hang
- [ ] No `time.sleep` (or other blocking sleeps) on the event loop
- [ ] No synchronous HTTP or file I/O inside async route functions

## Checklist — outbound vendor fan-out

- [ ] Fan-out is **best-effort** relative to primary ingest success
- [ ] Use a **shared** async HTTP client for the process (connection reuse)
- [ ] Every outbound call has **connect + read timeouts**
- [ ] Limit concurrent in-flight vendor calls (semaphore or worker pool)
- [ ] Partner/vendor slowness must not grow unbounded memory of pending payloads
- [ ] Retry policy: bounded attempts, exponential backoff, jitter; no infinite retry in-request

## Checklist — client lifecycle requirements

- [ ] Client created at application startup / lifespan
- [ ] Client closed on shutdown
- [ ] No “open client per sample” or “per request” on the hot path
- [ ] TLS / keep-alive expectations documented
- [ ] Choose httpx **or** aiohttp as primary for v1 (document choice); second may appear only as deliberate contrast later

## Checklist — performance anti-patterns this phase must call out

- [ ] Blocking `requests` inside async routes
- [ ] Missing timeouts on async clients
- [ ] New client session per call
- [ ] Awaiting fan-out inside the ingest response path when it should be queued
- [ ] Unbounded `create_task` without backpressure

## Checklist — observability for this phase

- [ ] Metrics: outbound success/fail, latency histogram, queue depth
- [ ] Metrics: event-loop lag / handler duration (planning level)
- [ ] Logs: never log full payloads at info on success path

## Checklist — acceptance for Phase 3 design

- [ ] Written rule set: “allowed vs forbidden on the event loop”
- [ ] Written fan-out architecture (inline vs queue) with rationale
- [ ] Timeout and concurrency numbers proposed (even if later tuned)
- [ ] Shutdown behavior described

## Exit criteria

- [ ] Async + client checklists complete
- [ ] Ready for Phase 4 (read path percentiles)
