import uuid
from datetime import datetime, timezone
from app.database import db

class Partner(db.Model):
    __tablename__ = "partners"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    api_key_hash = db.Column(db.String(64), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class PartnerEndpoint(db.Model):
    __tablename__ = "partner_endpoints"
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"), nullable=False, index=True)
    url = db.Column(db.String(1024), nullable=False)
    secret = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    circuit_until = db.Column(db.DateTime, nullable=True)
    circuit_failures = db.Column(db.Integer, default=0)
    concurrency_cap = db.Column(db.Integer, default=5)
    partner = db.relationship("Partner", backref="endpoints")

class InboundEvent(db.Model):
    __tablename__ = "inbound_events"
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(128), nullable=False)
    idempotency_key = db.Column(db.String(255), unique=True, nullable=True)
    payload = db.Column(db.Text, nullable=False)
    received_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.Index("ix_inbound_idempotency", "idempotency_key"),
    )

class DeliveryOutbox(db.Model):
    __tablename__ = "delivery_outbox"
    id = db.Column(db.Integer, primary_key=True)
    inbound_event_id = db.Column(db.Integer, db.ForeignKey("inbound_events.id"), nullable=False, index=True)
    partner_endpoint_id = db.Column(db.Integer, db.ForeignKey("partner_endpoints.id"), nullable=False, index=True)
    status = db.Column(db.String(32), default="PENDING")
    attempt_count = db.Column(db.Integer, default=0)
    next_attempt_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    inbound_event = db.relationship("InboundEvent", backref="deliveries")
    partner_endpoint = db.relationship("PartnerEndpoint", backref="deliveries")

    __table_args__ = (
        db.Index("ix_outbox_claim", "status", "next_attempt_at"),
    )

class DeliveryAttempt(db.Model):
    __tablename__ = "delivery_attempts"
    id = db.Column(db.Integer, primary_key=True)
    delivery_outbox_id = db.Column(db.Integer, db.ForeignKey("delivery_outbox.id"), nullable=False, index=True)
    attempt_number = db.Column(db.Integer, nullable=False)
    status_code = db.Column(db.Integer, nullable=True)
    response_body = db.Column(db.Text, nullable=True)
    latency_ms = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    attempted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    delivery = db.relationship("DeliveryOutbox", backref="attempts")

    __table_args__ = (
        db.Index("ix_attempts_outbox", "delivery_outbox_id"),
    )
