# Phase 5 — CodeHound detection targets (planning map)

**Status:** planning  
**Depends on:** Phases 1–4  
**Goal:** Map relay hot paths to future CodeHound Python / Flask / HTTP client themes.

## Checklist — PERF themes

- [ ] HTTP calls without timeouts
- [ ] New session/client per request in a loop
- [ ] String / JSON thrash inside retry loops
- [ ] Regex compile inside attempt loops
- [ ] open() without context manager on staging paths
- [ ] Unbounded loops over query results

## Checklist — framework / sync worker themes

- [ ] Doing fan-out HTTP inside the ingest request thread
- [ ] Sleeping while holding DB resources
- [ ] Unbounded in-memory queues in the Flask process

## Checklist — DB themes (integrated)

- [ ] Query-in-loop partner config loads
- [ ] Unbounded fetches of pending work

## Checklist — fixture strategy (later)

- [ ] Clean shared-session delivery worker
- [ ] Buggy no-timeout delivery loop
- [ ] Buggy per-iteration session create
- [ ] Buggy retry string-building module

## Checklist — out of scope first pack

- [ ] Full webhook security crypto audit
- [ ] Flask app factory style nits unrelated to perf

## Exit criteria

- [ ] Themes prioritized
- [ ] Ready for Phase 6
