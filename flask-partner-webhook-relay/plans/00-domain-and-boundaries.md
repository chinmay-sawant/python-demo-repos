# Phase 0 — Domain and boundaries

**Status:** planning  
**Project:** `flask-partner-webhook-relay`  
**Goal:** Lock a B2B webhook fan-out domain that stresses sync HTTP, retries, and integrated persistence.

## Checklist — domain lock

- [ ] Confirm product name: **Partner webhook relay for B2B integration events**
- [ ] Confirm upstream events: order / billing / shipment style notifications
- [ ] Confirm downstream: many partner HTTPS endpoints with per-partner secrets
- [ ] Confirm at-least-once delivery with idempotency keys for partners
- [ ] Confirm operator need: lag, failure rate, poison messages

## Checklist — actors

- [ ] Upstream producer systems
- [ ] Relay ingest API (Flask)
- [ ] Delivery workers (same codebase, sync model)
- [ ] Partner endpoints (slow, flaky, large)
- [ ] Operator / support

## Checklist — load shape

- [ ] Ingest burst size during merchant peak hours
- [ ] Average fan-out factor (partners per event)
- [ ] Partner p95 latency assumption
- [ ] Max payload size
- [ ] Max retry attempts and max age in queue

## Checklist — data concepts

- [ ] `Partner`
- [ ] `PartnerEndpoint`
- [ ] `InboundEvent`
- [ ] `DeliveryAttempt`
- [ ] `DeliveryOutbox` / work claim record

## Checklist — non-goals

- [ ] Replacing Kafka/SQS as enterprise bus (DB-backed outbox is OK for v1 plan)
- [ ] Async FastAPI rewrite (separate project)
- [ ] Partner transformation DSL of arbitrary complexity
- [ ] Code snippets in plans

## Exit criteria

- [ ] Problem statement agreed
- [ ] Load shape agreed
- [ ] Ready for Phase 1
