# Phase 4 — Persistence integrated (no separate DB project)

**Status:** planning  
**Depends on:** Phases 1–3  
**Goal:** Use the relational store as ingest durability + outbox + attempt audit inside this Flask app.

## Checklist — storage responsibilities

- [ ] Store inbound events
- [ ] Store per-partner delivery work items
- [ ] Store attempt history for ops and billing-grade audit (if required)
- [ ] Store partner endpoint config and secrets references (not raw secrets in logs)

## Checklist — performance requirements

- [ ] Claim work with bounded batch sizes
- [ ] Avoid query-per-partner-config inside tight loops when batching (prefetch config)
- [ ] Indexes for claim queries (`status`, `next_attempt_at`, `partner_id`)
- [ ] Attempt inserts should not dominate delivery latency (async batch log optional — document choice)
- [ ] Connection pool sizing vs worker count documented
- [ ] Long transactions forbidden around outbound HTTP

## Checklist — operational requirements

- [ ] Retention/purge for old events and attempts
- [ ] Metrics from DB state (queue depth) with cheap queries
- [ ] Migration notes for high-write attempt tables

## Checklist — anti-patterns

- [ ] Select all pending deliveries without limit
- [ ] Update row per attempt without index support
- [ ] Holding row locks while waiting on partner network

## Checklist — acceptance

- [ ] Table/purpose list in prose
- [ ] Claim algorithm summary
- [ ] Index list
- [ ] Worker × pool worksheet

## Exit criteria

- [ ] DB fully specified inside this project
- [ ] Ready for Phase 5
