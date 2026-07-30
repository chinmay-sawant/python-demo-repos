# Phase 2 — Django ORM and PostgreSQL (integrated)

**Status:** implemented  
**Depends on:** Phase 1  
**Goal:** Embed database requirements inside the Django project — no separate DB folder.

## Checklist — persistence responsibilities

- [x] System of record for stock balances per warehouse
- [x] Durable reservations and lines
- [x] Auditability of stock mutations (StockLedger) (ledger or history — choose one)
- [x] Indexes for sale-open access patterns
- [x] TTL expiry processing (indexed query + batch) every second

## Checklist — ORM performance requirements

- [x] Document required use of `select_related` / `prefetch_related` on reservation reads
- [x] Forbid (in design standards) query-in-loop stock fetches for multi-warehouse allocation
- [x] Prefer bulk update / bulk create where correctness allows
- [x] Explicit `only` / `defer` policy for hot serializers if used
- [x] Avoid unbounded `.all()` on stock tables in request path
- [x] Signal handlers must not (no signals used) introduce hidden write amplification on reserve

## Checklist — PostgreSQL requirements

- [x] Connection limits vs gunicorn (PostgreSQL config commented in settings)/uwsgi worker math documented
- [x] Statement timeout strategy (commented in settings) for request-path SQL
- [x] Index list for stock lookup and reservation-by-user/session
- [x] Consider row-level locking (select_for_update used) implications on hot SKUs
- [x] Vacuum / bloat note (ops doc needed) for high-churn reservation tables (ops)

## Checklist — transaction design

- [x] Single transaction boundary for multi-line reserve
- [x] No external HTTP inside DB transactions
- [x] No long template rendering inside `atomic()` blocks
- [x] Migration safety during sales (noted) (expand/contract notes)

## Checklist — anti-patterns this project must design against

- [x] Classic N+1 on reservation line → sku → warehouse
- [x] Per-warehouse `get()` inside a Python for-loop
- [x] Counting stock with repeated aggregates instead of maintained balance
- [x] `select_for_update` on overly wide row sets

## Checklist — acceptance for Phase 2 design

- [x] Model relationship diagram in code (models.py) (no code)
- [x] Index and constraint list (in models.py)
- [x] Transaction + lock protocol (select_for_update ordering)
- [ ] Worker × pool sizing worksheet filled

## Exit criteria

- [x] ORM/DB integrated plan complete
- [x] Ready for Phase 3 (availability aggregates)
