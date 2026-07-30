# Phase 2 — Django ORM and PostgreSQL (integrated)

**Status:** planning  
**Depends on:** Phase 1  
**Goal:** Embed database requirements inside the Django project — no separate DB folder.

## Checklist — persistence responsibilities

- [ ] System of record for stock balances per warehouse
- [ ] Durable reservations and lines
- [ ] Auditability of stock mutations (ledger or history — choose one)
- [ ] Indexes for sale-open access patterns
- [ ] TTL expiry processing without full table scans every second

## Checklist — ORM performance requirements

- [ ] Document required use of `select_related` / `prefetch_related` on reservation reads
- [ ] Forbid (in design standards) query-in-loop stock fetches for multi-warehouse allocation
- [ ] Prefer bulk update / bulk create where correctness allows
- [ ] Explicit `only` / `defer` policy for hot serializers if used
- [ ] Avoid unbounded `.all()` on stock tables in request path
- [ ] Signal handlers must not introduce hidden write amplification on reserve

## Checklist — PostgreSQL requirements

- [ ] Connection limits vs gunicorn/uwsgi worker math documented
- [ ] Statement timeout strategy for request-path SQL
- [ ] Index list for stock lookup and reservation-by-user/session
- [ ] Consider row-level locking implications on hot SKUs
- [ ] Vacuum / bloat note for high-churn reservation tables (ops)

## Checklist — transaction design

- [ ] Single transaction boundary for multi-line reserve
- [ ] No external HTTP inside DB transactions
- [ ] No long template rendering inside `atomic()` blocks
- [ ] Migration safety during sales (expand/contract notes)

## Checklist — anti-patterns this project must design against

- [ ] Classic N+1 on reservation line → sku → warehouse
- [ ] Per-warehouse `get()` inside a Python for-loop
- [ ] Counting stock with repeated aggregates instead of maintained balance
- [ ] `select_for_update` on overly wide row sets

## Checklist — acceptance for Phase 2 design

- [ ] Model relationship diagram in prose (no code)
- [ ] Index and constraint list
- [ ] Transaction + lock protocol
- [ ] Worker × pool sizing worksheet filled

## Exit criteria

- [ ] ORM/DB integrated plan complete
- [ ] Ready for Phase 3 (availability aggregates)
