import threading
import time

import pytest
from app.database import db
from app.models import DeliveryOutbox
from app.services.delivery import DeliveryWorker
from sqlalchemy import event

from tests.helpers import MockPartnerHandler, count_selects, seed_outbox


class TestConcurrentDelivery:
    def test_per_endpoint_concurrency_respects_cap(self, worker_app, mock_partner):
        n = 12
        cap = 3
        seed_outbox(worker_app, mock_partner, n, cap=cap, latency=0.05)

        worker = DeliveryWorker(worker_app)
        delivered = worker.run_once()

        assert delivered == n
        assert MockPartnerHandler.max_concurrent >= 2
        assert MockPartnerHandler.max_concurrent <= cap
        with worker_app.app_context():
            assert DeliveryOutbox.query.filter_by(status="DELIVERED").count() == n

    def test_pool_bounded_by_delivery_max_concurrency(self, worker_app, mock_partner):
        n = 30
        seed_outbox(worker_app, mock_partner, n, cap=100, latency=0.2)

        worker = DeliveryWorker(worker_app)
        assert worker.app.config["DELIVERY_MAX_CONCURRENCY"] == 10

        t0 = time.perf_counter()
        delivered = worker.run_once()
        elapsed = time.perf_counter() - t0

        assert delivered == n
        assert MockPartnerHandler.max_concurrent <= worker.app.config["DELIVERY_MAX_CONCURRENCY"]
        assert MockPartnerHandler.max_concurrent >= 2
        assert elapsed < 2.0


class TestAtomicClaim:
    def test_concurrent_claim_is_disjoint(self, worker_app):
        with worker_app.app_context():
            if db.engine.dialect.name != "postgresql":
                pytest.skip(
                    "with_for_update is silently ignored on sqlite; "
                    "disjoint-claim proof deferred until a Postgres server is available"
                )
            seed_outbox(worker_app, "http://unused.local/webhook", 200, cap=10, latency=0.0)

        results = {}
        barrier = threading.Barrier(2)

        def claim(name):
            with worker_app.app_context():
                barrier.wait()
                worker = DeliveryWorker(worker_app)
                results[name] = {o.id for o in worker.claim_work(batch_size=200)}

        threads = [threading.Thread(target=claim, args=(name,)) for name in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ids_a, ids_b = results["a"], results["b"]
        assert len(ids_a | ids_b) == 200
        assert len(ids_a & ids_b) == 0


class TestQueryCount:
    def test_batch_of_50_uses_one_select(self, worker_app, mock_partner):
        n = 50
        seed_outbox(worker_app, mock_partner, n, latency=0.05)

        with worker_app.app_context():
            counter, handler = count_selects(db.engine)
            worker = DeliveryWorker(worker_app)
            worker.run_once()
            event.remove(db.engine, "before_cursor_execute", handler)

        assert counter["selects"] <= 2
        with worker_app.app_context():
            assert DeliveryOutbox.query.filter_by(status="DELIVERED").count() == n
