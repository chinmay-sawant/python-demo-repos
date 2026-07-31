from datetime import UTC, datetime

from app.database import db
from app.models import DeliveryOutbox, InboundEvent, PartnerEndpoint


class IngestService:
    @staticmethod
    def process_event(*, event_type: str, payload: str, idempotency_key: str = None) -> dict:
        if idempotency_key:
            existing = InboundEvent.query.filter_by(idempotency_key=idempotency_key).first()
            if existing:
                return {"event_id": existing.id, "duplicate": True}

        event = InboundEvent(
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            received_at=datetime.now(UTC),
        )
        db.session.add(event)
        db.session.flush()

        endpoints = PartnerEndpoint.query.filter_by(is_active=True).all()
        deliveries = []
        for ep in endpoints:
            if ep.circuit_until and ep.circuit_until > datetime.now(UTC):
                continue
            outbox = DeliveryOutbox(
                inbound_event_id=event.id,
                partner_endpoint_id=ep.id,
                status="PENDING",
                next_attempt_at=datetime.now(UTC),
            )
            db.session.add(outbox)
            deliveries.append(outbox)

        db.session.commit()
        return {"event_id": event.id, "fan_out": len(deliveries), "duplicate": False}
