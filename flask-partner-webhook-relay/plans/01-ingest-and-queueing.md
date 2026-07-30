# Phase 1 — Ingest and work queueing

**Status:** implemented  
**Depends on:** Phase 0  
**Goal:** Accept bursts and turn them into durable delivery work without melting workers.

## Checklist — functional requirements

- [x] Authenticated ingest endpoint for upstream events
- [x] Validate schema and payload size limits
- [x] Persist event and enqueue fan-out work per subscribed partner
- [x] Deduplicate ingest by upstream idempotency key when present
- [x] Fast ACK to upstream once durable (define durability bar)

## Checklist — performance requirements

- [x] Ingest path avoids per-partner synchronous HTTP
- [x] Enqueue must be batch-friendly when many partners subscribe
- [x] Validation regex/schema work must not recompile patterns per field in a loop carelessly
- [x] Avoid giant in-memory copies of payload while writing multiple rows
- [x] Ingest latency must not wait on slowest partner

## Checklist — backpressure

- [x] Define max queue depth / max pending deliveries
- [x] Define reject vs shed policy when overloaded
- [x] Define poison event isolation

## Checklist — acceptance

- [x] Ingest contract written
- [x] Durability definition written
- [x] Overload policy written

## Exit criteria

- [x] Ready for Phase 2 (outbound delivery)
