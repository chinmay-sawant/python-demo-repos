import time
import logging
from datetime import datetime, timezone, timedelta
from flask import current_app
from app.database import db
from app.models import DeliveryOutbox, InboundEvent, DeliveryAttempt, PartnerEndpoint
from app.services.delivery import DeliveryWorker

logger = logging.getLogger(__name__)

def register_commands(app):
    @app.cli.command("run-worker")
    def run_worker():
        """Run the delivery worker loop."""
        worker = DeliveryWorker(app)
        poll_interval = app.config["DELIVERY_QUEUE_POLL_INTERVAL"]
        logger.info("Delivery worker started (poll every %ds)", poll_interval)
        try:
            while True:
                delivered = worker.run_once()
                if delivered:
                    logger.info("Delivered %d items", delivered)
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Worker shutting down...")
        finally:
            worker.cleanup()
            logger.info("Worker stopped")

    @app.cli.command("purge-old-data")
    def purge_old_data():
        """Delete events and attempts older than retention days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=app.config["RETENTION_DAYS"])

        old_attempts = DeliveryAttempt.query.filter(
            DeliveryAttempt.attempted_at < cutoff
        ).delete()

        old_outbox = DeliveryOutbox.query.filter(
            DeliveryOutbox.created_at < cutoff,
            DeliveryOutbox.status.in_(["DELIVERED", "DEAD_LETTER"]),
        ).delete()

        old_events = InboundEvent.query.filter(
            InboundEvent.received_at < cutoff
        ).delete()

        db.session.commit()
        print(f"Purged {old_attempts} attempts, {old_outbox} outbox rows, {old_events} events")

    @app.cli.command("redrive-dead-letter")
    def redrive_dead_letter():
        """Reset DEAD_LETTER items to PENDING for retry."""
        items = DeliveryOutbox.query.filter_by(status="DEAD_LETTER").all()
        count = 0
        for item in items:
            item.status = "PENDING"
            item.next_attempt_at = datetime.now(timezone.utc)
            item.attempt_count = 0
            item.last_error = None
            count += 1
        db.session.commit()
        print(f"Redrove {count} items")

    @app.cli.command("queue-depth")
    def queue_depth():
        """Print queue depth metrics."""
        now = datetime.now(timezone.utc)
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
