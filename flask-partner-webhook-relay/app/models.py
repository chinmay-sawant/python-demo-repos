from datetime import UTC, datetime
from typing import cast

from sqlalchemy.orm import Mapped

from app.database import Model, db


class Partner(Model):
    __tablename__ = "partners"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    api_key_hash = db.Column(db.String(64), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


class PartnerEndpoint(Model):
    __tablename__ = "partner_endpoints"
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"), nullable=False, index=True)
    url = db.Column(db.String(1024), nullable=False)
    secret = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    circuit_until = db.Column(db.DateTime, nullable=True)
    circuit_failures = db.Column(db.Integer, default=0)
    concurrency_cap = db.Column(db.Integer, default=5)
    partner: Mapped[Partner] = cast(
        Mapped[Partner], db.relationship("Partner", backref="endpoints")
    )


class InboundEvent(Model):
    __tablename__ = "inbound_events"
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(128), nullable=False)
    idempotency_key = db.Column(db.String(255), unique=True, nullable=True)
    payload = db.Column(db.Text, nullable=False)
    received_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("ix_inbound_idempotency", "idempotency_key"),
        db.Index("ix_inbound_received_at", "received_at"),
    )


class DeliveryOutbox(Model):
    __tablename__ = "delivery_outbox"
    id = db.Column(db.Integer, primary_key=True)
    inbound_event_id = db.Column(
        db.Integer, db.ForeignKey("inbound_events.id"), nullable=False, index=True
    )
    partner_endpoint_id = db.Column(
        db.Integer, db.ForeignKey("partner_endpoints.id"), nullable=False, index=True
    )
    status = db.Column(db.String(32), default="PENDING")
    attempt_count = db.Column(db.Integer, default=0)
    next_attempt_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    inbound_event: Mapped[InboundEvent] = cast(
        Mapped[InboundEvent], db.relationship("InboundEvent", backref="deliveries")
    )
    partner_endpoint: Mapped[PartnerEndpoint] = cast(
        Mapped[PartnerEndpoint], db.relationship("PartnerEndpoint", backref="deliveries")
    )

    __table_args__ = (
        db.Index("ix_outbox_claim", "status", "next_attempt_at"),
        db.Index("ix_outbox_created_at", "created_at"),
    )


class DeliveryAttempt(Model):
    __tablename__ = "delivery_attempts"
    id = db.Column(db.Integer, primary_key=True)
    delivery_outbox_id = db.Column(
        db.Integer, db.ForeignKey("delivery_outbox.id"), nullable=False, index=True
    )
    attempt_number = db.Column(db.Integer, nullable=False)
    status_code = db.Column(db.Integer, nullable=True)
    response_body = db.Column(db.Text, nullable=True)
    latency_ms = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    attempted_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    delivery: Mapped[DeliveryOutbox] = cast(
        Mapped[DeliveryOutbox], db.relationship("DeliveryOutbox", backref="attempts")
    )

    __table_args__ = (
        db.Index("ix_attempts_outbox", "delivery_outbox_id"),
        db.Index("ix_attempts_attempted_at", "attempted_at"),
    )
