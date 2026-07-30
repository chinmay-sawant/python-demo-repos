# codehound-python-perf-targets

Performance-sensitive **Python service requirements** used to shape CodeHound’s
Python PERF / framework-footgun catalog. This tree is **plans only** — no
application source, no snippets.

> Entire project authored by **DeepSeek V4 Flash**. Every requirement, plan, and
> acceptance criterion was generated with DeepSeek V4 Flash.

Parent path:

```text
/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets
```

## Project folders

| Folder | Stack focus | Use case |
|--------|-------------|----------|
| [`fastapi-live-metrics-ingest`](./fastapi-live-metrics-ingest/) | FastAPI, asyncio, SQLAlchemy async, httpx/aiohttp | Live SaaS metrics ingest + percentile read path |
| [`django-flash-sale-inventory`](./django-flash-sale-inventory/) | Django, Django ORM, PostgreSQL | Flash-sale multi-warehouse inventory reservation |
| [`flask-partner-webhook-relay`](./flask-partner-webhook-relay/) | Flask, sync workers, requests, relational store | B2B partner webhook fan-out with retries |

Database is **not** a separate project. Persistence requirements live inside each
framework plan (connection pooling, query-in-loop, transaction boundaries,
batch writes).

## Why these use cases (not generic CRUD)

Each domain forces **hot-path** work under load: high ingest QPS, concurrent
stock reservation, or outbound HTTP fan-out. Requirements deliberately surface
patterns CodeHound should detect later (regex thrash, string building in loops,
missing client timeouts, ORM N+1, blocking I/O in async routes, resource leaks).

## How to read a project

1. Project `README.md` — domain, SLOs, non-goals, tech boundary.
2. `plans/` — phase-wise **checklist** plans (requirements + acceptance only).

## Non-goals for this corpus

- Shipping runnable apps in this tree (plans first).
- Full security SAST coverage (PERF + framework footguns first).
- Replacing ruff / bandit / mypy.
- A standalone “database” or “SQLAlchemy-only” project folder.
