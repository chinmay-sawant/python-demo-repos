# Phase 5 — CodeHound detection targets (planning map)

**Status:** planning  
**Depends on:** Phases 1–4  
**Goal:** Map flash-sale hot paths to future CodeHound Python / Django themes.

## Checklist — PERF / ORM themes

- [ ] Query-in-loop / N+1 shaped ORM access
- [ ] Missing prefetch patterns on reservation graphs
- [ ] Per-item `get()` / `filter().first()` inside loops over cart lines
- [ ] Heavy string/regex work inside tight allocation loops
- [ ] Unbounded querysets evaluated in request path

## Checklist — Django framework footguns

- [ ] Expensive middleware on hot routes
- [ ] Signal handlers causing write amplification
- [ ] Work inside `atomic()` that should be outside (HTTP, sleep, huge CPU)
- [ ] Template/serializer lazy-load explosions (if statically visible)

## Checklist — DB themes (integrated in Django project)

- [ ] Connection-per-request anti-patterns (if expressible)
- [ ] Over-broad locking patterns (design review + future heuristics)

## Checklist — fixture strategy (later)

- [ ] Clean reservation service module
- [ ] Buggy N+1 allocation module
- [ ] Buggy middleware body-read module
- [ ] Document expected FN/FP for ORM heuristics (static analysis limits)

## Checklist — out of scope for first pack

- [ ] Full Django security checklist replacement
- [ ] Migrations correctness analyzer
- [ ] Admin-site performance pack

## Exit criteria

- [ ] Themes prioritized P0 / P1 / later
- [ ] Ready for Phase 6
