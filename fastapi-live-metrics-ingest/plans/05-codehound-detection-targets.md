# Phase 5 — CodeHound detection targets (planning map)

**Status:** implemented  
**Depends on:** Phases 1–4  
**Goal:** Map this project’s hot paths to **future** CodeHound Python rule themes.  
No rule IDs are final; this is a requirements → detector theme bridge.

> **Note:** Items below are CodeHound *detection rule* targets (what CodeHound should scan for), not FastAPI app implementation tasks. The app code is complete; these define future CodeHound heuristics.

## Checklist — PERF themes this project should exercise

- [ ] Regex compile / heavy match **inside loops** (UA and route normalization)
- [ ] Repeated expensive parsing in loops (`json` re-parse, datetime parse thrash)
- [ ] String building / formatting thrash on ingest batch iteration
- [ ] Blocking I/O or `sleep` inside async route / async task
- [ ] HTTP client missing timeouts
- [ ] HTTP client created per request / per call (no reuse)
- [ ] Unbounded task spawn without backpressure
- [ ] File or network resource opened without clear lifecycle on hot paths

## Checklist — framework footgun themes (FastAPI / Starlette)

- [ ] Blocking work in `async def` endpoints
- [ ] Dependency callables that hide blocking I/O
- [ ] Middleware that does heavy per-request work (body re-read, full JSON parse twice)
- [ ] Lifespan missing client/engine cleanup

## Checklist — DB / SQLAlchemy themes (integrated)

- [ ] Per-item insert in a Python loop instead of batch
- [ ] Lazy-load patterns that explode query count after async session use
- [ ] New engine/session factory patterns that defeat pooling
- [ ] Missing acquisition timeout / unbounded wait behavior (if expressible statically)

## Checklist — fixture strategy (later implementation, not now)

- [ ] Plan for **clean** variants (should not fire)
- [ ] Plan for **buggy** variants (should fire)
- [ ] Plan for **borderline** variants (document FN/FP expectations)
- [ ] Keep fixtures small and named by rule theme when coding starts

## Checklist — out of scope for first Python PERF pack

- [ ] Full security injection suite
- [ ] Typed-Python / mypy integration
- [ ] Whole-program taint across services

## Exit criteria

- [ ] Theme list prioritized (P0 / P1 / later)
- [ ] Ready for Phase 6 (acceptance and pilot)
