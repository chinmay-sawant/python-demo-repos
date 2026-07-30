# Phase 0 — Domain and boundaries

**Status:** implemented  
**Project:** `flask-partner-webhook-relay`  
**Goal:** Lock a B2B webhook fan-out domain that stresses sync HTTP, retries, and integrated persistence.

## Checklist — domain lock

- [x] Confirm product name: **Partner webhook relay for B2B integration events**
- [x] Confirm upstream events: order / billing / shipment style notifications
- [x] Confirm downstream: many partner HTTPS endpoints with per-partner secrets
- [x] Confirm at-least-once delivery with idempotency keys for partners
- [x] Confirm operator need: lag, failure rate, poison messages

## Checklist — actors

- [x] Upstream producer systems
- [x] Relay ingest API (Flask)
- [x] Delivery workers (same codebase, sync model)
- [x] Partner endpoints (slow, flaky, large)
- [x] Operator / support

## Checklist — load shape

- [x] Ingest burst size during merchant peak hours
- [x] Average fan-out factor (partners per event)
- [x] Partner p95 latency assumption
- [x] Max payload size
- [x] Max retry attempts and max age in queue

## Checklist — data concepts

- [x] `Partner`
- [x] `PartnerEndpoint`
- [x] `InboundEvent`
- [x] `DeliveryAttempt`
- [x] `DeliveryOutbox` / work claim record

## Checklist — non-goals

- [x] Replacing Kafka/SQS as enterprise bus (DB-backed outbox is OK for v1 plan)
- [x] Async FastAPI rewrite (separate project)
- [x] Partner transformation DSL of arbitrary complexity
- [x] Code snippets in plans

## Exit criteria

- [x] Problem statement agreed
- [x] Load shape agreed
- [x] Ready for Phase 1
