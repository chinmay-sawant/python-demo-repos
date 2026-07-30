# Phase 4 — Persistence integrated (no separate DB project)

**Status:** implemented  
**Depends on:** Phases 1–3  
**Goal:** Use the relational store as ingest durability + outbox + attempt audit inside this Flask app.

## Checklist — storage responsibilities

- [x] Store inbound events
- [x] Store per-partner delivery work items
- [x] Store attempt history for ops and billing-grade audit (if required)
- [x] Store partner endpoint config and secrets references (not raw secrets in logs)

## Checklist — performance requirements

- [x] Claim work with bounded batch sizes
- [x] Avoid query-per-partner-config inside tight loops when batching (prefetch config)
- [x] Indexes for claim queries (`status`, `next_attempt_at`, `partner_id`)
- [x] Attempt inserts should not dominate delivery latency (async batch log optional — document choice)
- [x] Connection pool sizing vs worker count documented
- [x] Long transactions forbidden around outbound HTTP

## Checklist — operational requirements

- [x] Retention/purge for old events and attempts
- [x] Metrics from DB state (queue depth) with cheap queries
- [x] Migration notes for high-write attempt tables

## Checklist — anti-patterns

- [x] Select all pending deliveries without limit
- [x] Update row per attempt without index support
- [x] Holding row locks while waiting on partner network

## Checklist — acceptance

- [x] Table/purpose list in prose
- [x] Claim algorithm summary
- [x] Index list
- [ ] Worker × pool worksheet

## Exit criteria

- [x] DB fully specified inside this project
- [x] Ready for Phase 5
