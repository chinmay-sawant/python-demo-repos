# Phase 3 — Availability reads and aggregates

**Status:** implemented  
**Depends on:** Phase 2  
**Goal:** Keep listing/PDP/ops stock reads cheap while reserve writes spike.

## Checklist — functional requirements

- [x] PDP: remaining sellable qty for a SKU (optionally by region)
- [x] Listing: batch availability for many SKUs on a collection page
- [x] Ops: per-warehouse remaining for a sale event
- [ ] Optional: “low stock” badges without extra round-trips per card

## Checklist — performance requirements

- [x] Collection page must not issue one query per product card
- [x] Aggregates must have a defined freshness (live vs slightly stale)
- [x] Cache (not implemented, v1 primary-only) keys and TTL rules documented; stampede behavior considered
- [x] Ops rollups must not run unconstrained group-by on entire history mid-sale
- [x] Read replicas optional (v1 primary-only) later — v1 must state primary-only assumptions

## Checklist — consistency requirements

- [x] Define whether availability may show stale positive stock briefly
- [x] Define that reserve path remains the correctness gate even if badge is stale
- [x] Define sold-out transition visibility

## Checklist — anti-patterns

- [x] Template or serializer that triggers lazy ORM per item
- [x] Recomputing global sums in Python over large querysets
- [x] Building giant in-memory dicts of all SKUs every request

## Checklist — acceptance

- [x] Read APIs / view responsibilities listed
- [x] Query budget per page type written
- [x] Freshness policy written

## Exit criteria

- [x] Ready for Phase 4 (middleware / request cost)
