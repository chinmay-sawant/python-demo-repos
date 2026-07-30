# flask-partner-webhook-relay

**Stack:** Flask · sync workers · requests (or similar) · relational store  
**Plans only:** see [`plans/`](./plans/)

## Domain (specific, performance-sensitive)

Build a **B2B partner webhook relay** for an integration platform.

Upstream systems (orders, billing events, shipment updates) post events into
the relay. The relay must:

1. Validate and persist the event briefly for retry/audit.
2. **Fan out** signed HTTPS deliveries to many partner endpoints.
3. Retry with backoff on partner 5xx / timeouts without collapsing the worker
   process.
4. Expose operator views for delivery lag and failure rates.

This is **not** a generic employee CRUD app. The hard problem is **sync HTTP
fan-out under backlog**: missing timeouts, new TCP connections per attempt,
string building in retry loops, open file/handle thrash for payload staging,
and DB writes that double as an accidental queue without batching.

## Primary performance SLOs (planning targets)

| Path | Target direction |
|------|------------------|
| Ingest | Accept bursts; durable enough for retry |
| Delivery | Sustained fan-out QPS; partner slowness must isolate |
| Retry | Bounded concurrency; no retry storms against one partner |
| DB | Delivery attempt logging must not dominate latency |

## Technologies in scope (integrated, not separate folders)

- Flask request handlers and WSGI worker model
- Sync outbound HTTP (`requests` / urllib3 pool semantics)
- Relational persistence for events, delivery attempts, partner config
- Signature headers and payload re-serialization on hot retry paths

## Explicit non-goals

- Replacing a full message bus (Kafka) in v1 of the plan
- Async rewrite to FastAPI (that is a different project folder)
- Code samples in these plans

## Plan index

| Phase | File | Theme |
|-------|------|--------|
| 0 | [`plans/00-domain-and-boundaries.md`](./plans/00-domain-and-boundaries.md) | Domain lock, actors, non-goals |
| 1 | [`plans/01-ingest-and-queueing.md`](./plans/01-ingest-and-queueing.md) | Accept, persist, claim work |
| 2 | [`plans/02-outbound-delivery.md`](./plans/02-outbound-delivery.md) | Timeouts, pools, fan-out isolation |
| 3 | [`plans/03-retry-and-payload-cost.md`](./plans/03-retry-and-payload-cost.md) | Retry loops, serialization thrash |
| 4 | [`plans/04-persistence-integrated.md`](./plans/04-persistence-integrated.md) | DB as audit + work ledger |
| 5 | [`plans/05-codehound-detection-targets.md`](./plans/05-codehound-detection-targets.md) | What CodeHound should eventually flag |
| 6 | [`plans/06-acceptance-and-pilot.md`](./plans/06-acceptance-and-pilot.md) | Pilot checklist before implementation |
