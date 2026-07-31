import time
from datetime import UTC, datetime, timedelta

import pytest
from app.database import db
from app.models import DeliveryAttempt, DeliveryOutbox, InboundEvent, PartnerEndpoint
from app.services.delivery import DeliveryWorker


def seed_purge_rows(app):
    old = datetime.now(UTC) - timedelta(days=60)
    recent = datetime.now(UTC)
    with app.app_context():
        endpoint = PartnerEndpoint.query.first()
        for _i in range(3):
            event = InboundEvent(event_type="old", payload="{}", received_at=old)
            db.session.add(event)
            db.session.flush()
            outbox = DeliveryOutbox(
                inbound_event_id=event.id,
                partner_endpoint_id=endpoint.id,
                status="DELIVERED",
                created_at=old,
            )
            db.session.add(outbox)
            db.session.flush()
            db.session.add(
                DeliveryAttempt(
                    delivery_outbox_id=outbox.id,
                    attempt_number=1,
                    attempted_at=old,
                )
            )
        event = InboundEvent(event_type="recent", payload="{}", received_at=recent)
        db.session.add(event)
        db.session.flush()
        outbox = DeliveryOutbox(
            inbound_event_id=event.id,
            partner_endpoint_id=endpoint.id,
            status="PENDING",
            created_at=recent,
        )
        db.session.add(outbox)
        db.session.flush()
        db.session.add(
            DeliveryAttempt(
                delivery_outbox_id=outbox.id,
                attempt_number=1,
                attempted_at=recent,
            )
        )
        db.session.commit()


def seed_dead_letter_rows(app, n, *, keep_pending=1):
    with app.app_context():
        endpoint = PartnerEndpoint.query.first()
        for i in range(n + keep_pending):
            event = InboundEvent(event_type="order.created", payload="{}")
            db.session.add(event)
            db.session.flush()
            status = "DEAD_LETTER" if i < n else "PENDING"
            db.session.add(
                DeliveryOutbox(
                    inbound_event_id=event.id,
                    partner_endpoint_id=endpoint.id,
                    status=status,
                    last_error="boom" if status == "DEAD_LETTER" else None,
                    attempt_count=5 if status == "DEAD_LETTER" else 0,
                )
            )
        db.session.commit()


class TestPurge:
    def test_purge_old_data(self, app, sample_partner):
        seed_purge_rows(app)
        runner = app.test_cli_runner()
        result = runner.invoke(args=["purge-old-data"])
        assert result.exit_code == 0
        assert "Purged 3 attempts, 3 outbox rows, 3 events" in result.output
        with app.app_context():
            assert InboundEvent.query.count() == 1
            assert DeliveryOutbox.query.count() == 1
            assert DeliveryAttempt.query.count() == 1
            assert DeliveryOutbox.query.filter_by(status="PENDING").count() == 1

    def test_purge_predicates_use_indexes(self, app, sample_partner):
        seed_purge_rows(app)
        with app.app_context():
            with db.engine.connect() as conn:
                for sql in [
                    "EXPLAIN QUERY PLAN SELECT id FROM inbound_events WHERE received_at < ?",
                    "EXPLAIN QUERY PLAN SELECT id FROM delivery_attempts WHERE attempted_at < ?",
                ]:
                    plan = conn.exec_driver_sql(
                        sql,
                        (datetime.now(UTC),),
                    ).fetchall()
                    assert "INDEX" in " ".join(row[3] for row in plan), plan
                plan = conn.exec_driver_sql(
                    "EXPLAIN QUERY PLAN SELECT id FROM delivery_outbox "
                    "WHERE created_at < ? AND status IN ('DELIVERED', 'DEAD_LETTER')",
                    (datetime.now(UTC),),
                ).fetchall()
                assert "INDEX" in " ".join(row[3] for row in plan), plan


class TestWorkerLoop:
    def test_second_run_starts_immediately_after_non_empty_run(self, app, monkeypatch):
        from app.cli import _worker_loop

        worker = DeliveryWorker(app)
        runs = {"n": 0}
        sleeps = {"n": 0}

        def fake_run_once():
            runs["n"] += 1
            return 50 if runs["n"] == 1 else 0

        def fake_sleep(_):
            sleeps["n"] += 1
            raise RuntimeError("stop")

        monkeypatch.setattr(worker, "run_once", fake_run_once)
        monkeypatch.setattr(time, "sleep", fake_sleep)

        with pytest.raises(RuntimeError):
            _worker_loop(worker, poll_interval=5)
        assert runs["n"] == 2
        assert sleeps["n"] == 1


class TestRedrive:
    def test_redrive_dead_letter_bulk(self, app, sample_partner):
        seed_dead_letter_rows(app, n=5)
        runner = app.test_cli_runner()
        result = runner.invoke(args=["redrive-dead-letter"])
        assert result.exit_code == 0
        assert "Redrove 5 items" in result.output
        with app.app_context():
            assert DeliveryOutbox.query.filter_by(status="DEAD_LETTER").count() == 0
            pending = DeliveryOutbox.query.filter_by(status="PENDING").all()
            assert len(pending) == 6
            for outbox in pending:
                assert outbox.attempt_count == 0
                assert outbox.last_error is None
