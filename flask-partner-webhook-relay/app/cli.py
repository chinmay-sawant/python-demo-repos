import logging
import time
from datetime import UTC, datetime, timedelta

from app.database import db
from app.models import DeliveryAttempt, DeliveryOutbox, InboundEvent
from app.services.delivery import DeliveryWorker

logger = logging.getLogger(__name__)


def _worker_loop(worker, poll_interval):
    while True:
        delivered = worker.run_once()
        if delivered:
            logger.info("Delivered %d items", delivered)
            continue
        time.sleep(poll_interval)


def _purge_chunks(base_query, order_col, chunk_size=1000):
    total = 0
    while True:
        ids = [
            row[0]
            for row in base_query.with_entities(order_col).order_by(order_col).limit(chunk_size)
        ]
        if not ids:
            break
        base_query.filter(order_col.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        total += len(ids)
    return total


def register_commands(app):
    @app.cli.command("run-worker")
    def run_worker():
        """Run the delivery worker loop."""
        worker = DeliveryWorker(app)
        poll_interval = app.config["DELIVERY_QUEUE_POLL_INTERVAL"]
        logger.info("Delivery worker started (poll every %ds)", poll_interval)
        try:
            _worker_loop(worker, poll_interval)
        except KeyboardInterrupt:
            logger.info("Worker shutting down...")
        finally:
            worker.cleanup()
            logger.info("Worker stopped")

    @app.cli.command("purge-old-data")
    def purge_old_data():
        """Delete events and attempts older than retention days."""
        cutoff = datetime.now(UTC) - timedelta(days=app.config["RETENTION_DAYS"])

        old_attempts = _purge_chunks(
            DeliveryAttempt.query.filter(DeliveryAttempt.attempted_at < cutoff),
            DeliveryAttempt.id,
        )

        old_outbox = _purge_chunks(
            DeliveryOutbox.query.filter(
                DeliveryOutbox.created_at < cutoff,
                DeliveryOutbox.status.in_(["DELIVERED", "DEAD_LETTER"]),
            ),
            DeliveryOutbox.id,
        )

        old_events = _purge_chunks(
            InboundEvent.query.filter(InboundEvent.received_at < cutoff),
            InboundEvent.id,
        )

        print(f"Purged {old_attempts} attempts, {old_outbox} outbox rows, {old_events} events")

    @app.cli.command("redrive-dead-letter")
    def redrive_dead_letter():
        """Reset DEAD_LETTER items to PENDING for retry."""
        count = DeliveryOutbox.query.filter_by(status="DEAD_LETTER").update(
            {
                "status": "PENDING",
                "next_attempt_at": datetime.now(UTC),
                "attempt_count": 0,
                "last_error": None,
            }
        )
        db.session.commit()
        print(f"Redrove {count} items")

    @app.cli.command("queue-depth")
    def queue_depth():
        """Print queue depth metrics."""
        now = datetime.now(UTC)
        stats = {
            "pending": DeliveryOutbox.query.filter(
                DeliveryOutbox.status == "PENDING",
                DeliveryOutbox.next_attempt_at <= now,
            ).count(),
            "in_flight": DeliveryOutbox.query.filter_by(status="IN_FLIGHT").count(),
            "failed": DeliveryOutbox.query.filter_by(status="FAILED").count(),
            "dead_letter": DeliveryOutbox.query.filter_by(status="DEAD_LETTER").count(),
            "delivered": DeliveryOutbox.query.filter_by(status="DELIVERED").count(),
        }
        print(f"Queue depth: {stats}")
