import argparse
import os
import resource
import time
from datetime import UTC, datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/opencode/bench_maintenance.db")

from app import create_app
from app.database import db
from app.models import DeliveryAttempt, DeliveryOutbox, InboundEvent, Partner, PartnerEndpoint

DB_PATH = "/tmp/opencode/bench_maintenance.db"
N_ROWS = 100_000
CHUNK = 5_000


def make_app():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    return create_app()


def seed_purge_data(app):
    old = datetime.now(UTC) - timedelta(days=60)
    with app.app_context():
        partner = Partner(name="Maintenance Bench", api_key_hash="bench-hash", is_active=True)
        db.session.add(partner)
        db.session.flush()
        endpoint = PartnerEndpoint(
            partner_id=partner.id,
            url="http://127.0.0.1:8200/webhook",
            secret="bench-secret",
            is_active=True,
        )
        db.session.add(endpoint)
        db.session.flush()
        event = InboundEvent(event_type="order.created", payload="{}", received_at=old)
        db.session.add(event)
        db.session.flush()
        for start in range(0, N_ROWS, CHUNK):
            size = min(CHUNK, N_ROWS - start)
            db.session.execute(db.insert(DeliveryOutbox), [
                {
                    "inbound_event_id": event.id,
                    "partner_endpoint_id": endpoint.id,
                    "status": "DELIVERED",
                    "attempt_count": 1,
                    "created_at": old,
                }
                for _ in range(size)
            ])
            db.session.execute(db.insert(DeliveryAttempt), [
                {
                    "delivery_outbox_id": start + i + 1,
                    "attempt_number": 1,
                    "status_code": 200,
                    "attempted_at": old,
                }
                for i in range(size)
            ])
        db.session.execute(db.insert(InboundEvent), [
            {"event_type": "order.created", "payload": "{}", "received_at": old}
            for _ in range(N_ROWS)
        ])
        db.session.commit()
    print(f"seeded {N_ROWS} outbox rows + {N_ROWS} attempts + {N_ROWS + 1} events (60d old)")


def seed_redrive_data(app):
    with app.app_context():
        partner = Partner(name="Maintenance Bench", api_key_hash="bench-hash", is_active=True)
        db.session.add(partner)
        db.session.flush()
        endpoint = PartnerEndpoint(
            partner_id=partner.id,
            url="http://127.0.0.1:8200/webhook",
            secret="bench-secret",
            is_active=True,
        )
        db.session.add(endpoint)
        db.session.flush()
        event = InboundEvent(event_type="order.created", payload="{}")
        db.session.add(event)
        db.session.flush()
        for start in range(0, N_ROWS, CHUNK):
            size = min(CHUNK, N_ROWS - start)
            db.session.execute(db.insert(DeliveryOutbox), [
                {
                    "inbound_event_id": event.id,
                    "partner_endpoint_id": endpoint.id,
                    "status": "DEAD_LETTER",
                    "attempt_count": 5,
                    "last_error": "HTTP 500",
                }
                for _ in range(size)
            ])
        db.session.commit()
    print(f"seeded {N_ROWS} DEAD_LETTER outbox rows")


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def bench_purge():
    app = make_app()
    seed_purge_data(app)
    t0 = time.perf_counter()
    result = app.test_cli_runner().invoke(args=["purge-old-data"])
    elapsed = time.perf_counter() - t0
    with app.app_context():
        remaining = {
            "events": InboundEvent.query.count(),
            "outbox": DeliveryOutbox.query.count(),
            "attempts": DeliveryAttempt.query.count(),
        }
    print(f"purge: {elapsed:.2f}s, exit={result.exit_code}, rss={rss_mb():.0f} MB, remaining={remaining}")
    print(result.output.strip())


def bench_redrive():
    app = make_app()
    seed_redrive_data(app)
    t0 = time.perf_counter()
    result = app.test_cli_runner().invoke(args=["redrive-dead-letter"])
    elapsed = time.perf_counter() - t0
    with app.app_context():
        dead = DeliveryOutbox.query.filter_by(status="DEAD_LETTER").count()
        pending = DeliveryOutbox.query.filter_by(status="PENDING").count()
    print(f"redrive: {elapsed:.2f}s, exit={result.exit_code}, rss={rss_mb():.0f} MB, "
          f"dead_letter={dead}, pending={pending}")
    print(result.output.strip())


def main():
    parser = argparse.ArgumentParser(description="Maintenance command benchmarks")
    parser.add_argument("command", choices=["purge", "redrive"])
    args = parser.parse_args()
    if args.command == "purge":
        bench_purge()
    else:
        bench_redrive()


if __name__ == "__main__":
    main()
