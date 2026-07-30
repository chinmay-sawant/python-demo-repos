# Phase 3 — Availability reads and aggregates

**Status:** planning  
**Depends on:** Phase 2  
**Goal:** Keep listing/PDP/ops stock reads cheap while reserve writes spike.

## Checklist — functional requirements

- [ ] PDP: remaining sellable qty for a SKU (optionally by region)
- [ ] Listing: batch availability for many SKUs on a collection page
- [ ] Ops: per-warehouse remaining for a sale event
- [ ] Optional: “low stock” badges without extra round-trips per card

## Checklist — performance requirements

- [ ] Collection page must not issue one query per product card
- [ ] Aggregates must have a defined freshness (live vs slightly stale)
- [ ] Cache (if introduced) keys and TTL rules documented; stampede behavior considered
- [ ] Ops rollups must not run unconstrained group-by on entire history mid-sale
- [ ] Read replicas optional later — v1 must state primary-only assumptions

## Checklist — consistency requirements

- [ ] Define whether availability may show stale positive stock briefly
- [ ] Define that reserve path remains the correctness gate even if badge is stale
- [ ] Define sold-out transition visibility

## Checklist — anti-patterns

- [ ] Template or serializer that triggers lazy ORM per item
- [ ] Recomputing global sums in Python over large querysets
- [ ] Building giant in-memory dicts of all SKUs every request

## Checklist — acceptance

- [ ] Read APIs / view responsibilities listed
- [ ] Query budget per page type written
- [ ] Freshness policy written

## Exit criteria

- [ ] Ready for Phase 4 (middleware / request cost)
