# Phase 4 — Middleware and per-request cost

**Status:** planning  
**Depends on:** Phases 1–3  
**Goal:** Control fixed cost paid on every flash-sale request outside the view body.

## Checklist — request pipeline requirements

- [ ] Inventory middleware/auth must be cheap on cacheable availability GETs
- [ ] Session/auth work documented; anonymous browse path stays light
- [ ] Avoid per-request full settings / feature-flag remote fetches on hot paths
- [ ] Avoid middleware that reads full body when only headers are needed
- [ ] Error reporting hooks must not block on network inside the request thread without timeout

## Checklist — serialization and templating (if used)

- [ ] JSON serialization must not re-fetch ORM relations
- [ ] Prefer explicit shaped DTOs over model instances leaking into templates
- [ ] Pagination mandatory on any list that can grow with sale size

## Checklist — background work interaction

- [ ] TTL release job must not lock the entire stock table
- [ ] Job schedule frequency vs sale duration documented
- [ ] Job batch sizes bounded

## Checklist — acceptance

- [ ] Middleware chain inventory listed with cost notes
- [ ] Hot GET vs hot POST cost asymmetry acknowledged
- [ ] Job vs request lock interaction documented

## Exit criteria

- [ ] Ready for Phase 5 (CodeHound targets)
