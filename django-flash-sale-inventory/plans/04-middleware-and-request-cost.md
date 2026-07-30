# Phase 4 — Middleware and per-request cost

**Status:** implemented  
**Depends on:** Phases 1–3  
**Goal:** Control fixed cost paid on every flash-sale request outside the view body.

## Checklist — request pipeline requirements

- [x] Inventory middleware/auth must be cheap (no-DB middleware) on cacheable availability GETs
- [x] Session/auth work documented; anonymous browse path stays light
- [x] Avoid per-request full settings / feature-flag remote fetches on hot paths
- [x] Avoid middleware that reads full body when only headers are needed
- [x] Error reporting hooks (logging only) must not block on network inside the request thread without timeout

## Checklist — serialization and templating (if used)

- [x] JSON serialization must not re-fetch ORM relations
- [x] Prefer explicit shaped DTOs over model instances leaking into templates
- [x] Pagination mandatory (noted for future) on any list that can grow with sale size

## Checklist — background work interaction

- [x] TTL release job must not lock (skip_locked) the entire stock table
- [x] Job schedule frequency (documented in command) vs sale duration documented
- [x] Job batch sizes bounded (200 default)

## Checklist — acceptance

- [x] Middleware chain inventory (listed with cost notes) listed with cost notes
- [x] Hot GET vs hot POST cost asymmetry acknowledged
- [x] Job vs request lock interaction (skip_locked documented) documented

## Exit criteria

- [x] Ready for Phase 5 (CodeHound targets)
