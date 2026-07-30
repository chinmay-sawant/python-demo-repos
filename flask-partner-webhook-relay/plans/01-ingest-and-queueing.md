# Phase 1 — Ingest and work queueing

**Status:** planning  
**Depends on:** Phase 0  
**Goal:** Accept bursts and turn them into durable delivery work without melting workers.

## Checklist — functional requirements

- [ ] Authenticated ingest endpoint for upstream events
- [ ] Validate schema and payload size limits
- [ ] Persist event and enqueue fan-out work per subscribed partner
- [ ] Deduplicate ingest by upstream idempotency key when present
- [ ] Fast ACK to upstream once durable (define durability bar)

## Checklist — performance requirements

- [ ] Ingest path avoids per-partner synchronous HTTP
- [ ] Enqueue must be batch-friendly when many partners subscribe
- [ ] Validation regex/schema work must not recompile patterns per field in a loop carelessly
- [ ] Avoid giant in-memory copies of payload while writing multiple rows
- [ ] Ingest latency must not wait on slowest partner

## Checklist — backpressure

- [ ] Define max queue depth / max pending deliveries
- [ ] Define reject vs shed policy when overloaded
- [ ] Define poison event isolation

## Checklist — acceptance

- [ ] Ingest contract written
- [ ] Durability definition written
- [ ] Overload policy written

## Exit criteria

- [ ] Ready for Phase 2 (outbound delivery)
