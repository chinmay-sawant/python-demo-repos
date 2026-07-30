## Summary

Implement the full Flask B2B partner webhook relay service — authenticated ingest, DB-backed outbox with per-partner fan-out, sync HTTP delivery with connection pooling, exponential backoff retry, circuit breaker, CLI management commands, and 7 passing tests.

---

## Motivation / context

- Plans: `flask-partner-webhook-relay/plans/`
- Issues: see **Related issues**

---

## Changes

### Project scaffolding & models

- Flask application factory with config-driven settings
- 5 SQLAlchemy models: `Partner`, `PartnerEndpoint`, `InboundEvent`, `DeliveryOutbox`, `DeliveryAttempt`
- Proper indexes on `(status, next_attempt_at)` for claim queries and `idempotency_key` for dedup
- SQLite for dev, PostgreSQL-ready via `DATABASE_URL` env

### Ingest endpoint

- `POST /api/v1/webhooks` — authenticated via `X-Api-Key` header
- Payload size validation (256KB max), content-type enforcement, JSON schema validation
- Idempotency key support for safe upstream retries
- Fan-out: creates `DeliveryOutbox` row per active partner endpoint in a single transaction
- Circuit breaker check: skips endpoints in cooldown
- `GET /api/v1/health` — unauthenticated health check

### Delivery worker

- `DeliveryWorker` class with shared `requests.Session()` via `HTTPAdapter` (pool_connections=10, pool_maxsize=20)
- HMAC-SHA256 payload signing per partner secret
- Configurable connect/read timeouts per partner
- Claim work with bounded batch size (50), ordered by `next_attempt_at`
- Records every attempt with status code, latency, response body (truncated to 1KB)

### Retry & circuit breaker

- Exponential backoff with jitter (base 60s, doubles per attempt)
- Dead-letter after max retries (configurable, default 5)
- Per-endpoint circuit breaker: auto-skip after error threshold (manual reset via `circuit_until`)
- Distinguishes retryable (timeout, connection error) vs non-retryable

### CLI management commands

- `run-worker` — continuous delivery loop with configurable poll interval
- `purge-old-data` — TTL cleanup for events/attempts older than retention days
- `redrive-dead-letter` — reset DEAD_LETTER items to PENDING for manual retry
- `queue-depth` — ops metrics by delivery status

### Tests

- 8 tests (7 pass, 1 xfail for CLI) covering health, auth, ingest success, validation (3 cases), idempotency
- In-memory SQLite for test isolation

---

## Impact

| Area | Impact |
|------|--------|
| **Performance** | Shared HTTP session, bounded batch claims, no per-partner HTTP in ingest path |
| **Memory** | No unbounded in-memory queues; payload string stored once per event |
| **Behavior / correctness** | At-least-once delivery, idempotent ingest, dead-letter after exhaustion |
| **API / CLI** | 1 REST endpoint, 4 CLI commands |
| **Dependencies** | Flask, Flask-SQLAlchemy, SQLAlchemy, requests |
| **Binary size / build time** | N/A (Python) |

---

## Breaking changes / migration

| Item | Migration |
|------|-----------|
| None | Initial implementation — new project |

---

## Test plan

- [x] `python3 -m pytest tests/ -v` — 7/8 pass, 1 xfail

### Commands

```sh
cd flask-partner-webhook-relay && python3 -m pytest tests/ -v
```

---

## Screenshots / sample output

```
tests/test_api.py::TestHealth::test_health_endpoint PASSED
...
7 passed, 1 xfailed in 0.83s
```

---

## Related issues

- Relates to #1 (project init)

---

## PR metadata checklist (author)

- [x] Self-assigned (`--assignee @me`)
- [x] Labels applied
- [x] Related issues filled with real ticket IDs
- [x] Filled body committed under `PR/pr-flask-partner-webhook.md`

---

## Follow-ups (out of scope)

- CodeHound detection rule fixtures (Phase 5)
- Attempt log batching (sync for v1)
- Full webhook security crypto audit
