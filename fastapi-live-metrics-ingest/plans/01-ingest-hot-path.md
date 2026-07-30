# Phase 1 — Ingest hot path

**Status:** planning  
**Depends on:** Phase 0  
**Goal:** Specify performance-sensitive requirements for accepting metric batches under burst load.

## Checklist — functional requirements (ingest only)

- [ ] Accept batch ingest endpoint for metric samples
- [ ] Reject oversized batches with a clear client error (bounded work)
- [ ] Support optional idempotency key for safe agent retries
- [ ] Attach `tenant_id` from auth context; never trust body tenancy alone
- [ ] Normalize route labels (strip query strings / high-cardinality IDs) **without** unbounded per-sample cost
- [ ] Classify user-agent into coarse buckets for dashboards

## Checklist — performance requirements

- [ ] Parsing and validation cost must be **O(batch)** with a hard cap on batch size
- [ ] Label normalization must not recompile regex patterns **per sample** or **per loop iteration**
- [ ] Avoid repeated JSON re-encoding/decoding of the same payload on the success path
- [ ] Avoid building large diagnostic strings on the success path (only on errors / debug)
- [ ] Ingest handler must not perform **blocking** disk or network I/O on the event loop
- [ ] Memory: no unbounded in-memory buffer of “pending forever” samples inside the request

## Checklist — failure / backpressure

- [ ] Define behavior when DB pool is saturated (fail fast vs short queue — pick one for v1)
- [ ] Define behavior when vendor fan-out is down (must **not** fail primary ingest)
- [ ] Emit counters for dropped samples, rejected batches, and validation failures
- [ ] Timeouts: request body read and total handler budget must be bounded

## Checklist — hot-path anti-patterns this phase must force into design review

These are requirements for the **future app** to *exercise* deliberately (good and bad variants later for CodeHound):

- [ ] User-agent / route regex classification on every sample
- [ ] String concatenation or repeated formatting while iterating samples
- [ ] Per-sample synchronous work that should be batched
- [ ] Accidental logging of full batch bodies at info level

## Checklist — acceptance for Phase 1 design

- [ ] Written contract: max batch size, max field sizes, max label length
- [ ] Written list of validation rules that run before persistence
- [ ] Written statement of what is **never** done in the request coroutine
- [ ] Open questions list (if any) with owner — no silent “TBD forever”

## Exit criteria

- [ ] Ingest contract checklist complete
- [ ] Performance constraints checklist complete
- [ ] Ready for Phase 2 (persistence / pool)
