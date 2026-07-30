# Phase 1 — Ingest hot path

**Status:** implemented  
**Depends on:** Phase 0  
**Goal:** Specify performance-sensitive requirements for accepting metric batches under burst load.

## Checklist — functional requirements (ingest only)

- [x] Accept batch ingest endpoint for metric samples
- [x] Reject oversized batches with a clear client error (bounded work)
- [x] Support optional idempotency key for safe agent retries
- [x] Attach `tenant_id` from auth context; never trust body tenancy alone
- [x] Normalize route labels (strip query strings / high-cardinality IDs) **without** unbounded per-sample cost
- [x] Classify user-agent into coarse buckets for dashboards

## Checklist — performance requirements

- [x] Parsing and validation cost must be **O(batch)** with a hard cap on batch size
- [x] Label normalization must not recompile regex patterns **per sample** or **per loop iteration**
- [x] Avoid repeated JSON re-encoding/decoding of the same payload on the success path
- [x] Avoid building large diagnostic strings on the success path (only on errors / debug)
- [x] Ingest handler must not perform **blocking** disk or network I/O on the event loop
- [x] Memory: no unbounded in-memory buffer of “pending forever” samples inside the request

## Checklist — failure / backpressure

- [x] Define behavior when DB pool is saturated (fail fast vs short queue — pick one for v1)
- [x] Define behavior when vendor fan-out is down (must **not** fail primary ingest)
- [x] Emit counters for dropped samples, rejected batches, and validation failures
- [x] Timeouts: request body read and total handler budget must be bounded

## Checklist — hot-path anti-patterns this phase must force into design review

These are requirements for the **future app** to *exercise* deliberately (good and bad variants later for CodeHound):

- [x] User-agent / route regex classification on every sample
- [x] String concatenation or repeated formatting while iterating samples
- [x] Per-sample synchronous work that should be batched
- [x] Accidental logging of full batch bodies at info level

## Checklist — acceptance for Phase 1 design

- [x] Written contract: max batch size, max field sizes, max label length
- [x] Written list of validation rules that run before persistence
- [x] Written statement of what is **never** done in the request coroutine
- [x] Open questions list (if any) with owner — no silent “TBD forever”

## Exit criteria

- [x] Ingest contract checklist complete
- [x] Performance constraints checklist complete
- [x] Ready for Phase 2 (persistence / pool)
