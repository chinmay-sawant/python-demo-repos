import hashlib
import hmac
import json
import logging
import time
import random
from datetime import datetime, timezone, timedelta
from sqlalchemy import and_
import requests
from app.database import db
from app.models import DeliveryOutbox, DeliveryAttempt, PartnerEndpoint

logger = logging.getLogger(__name__)

class DeliveryWorker:
    def __init__(self, app):
        self.app = app
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _sign_payload(self, payload_bytes: bytes, secret: str) -> str:
        return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    def claim_work(self, batch_size: int = 50) -> list:
        now = datetime.now(timezone.utc)
        items = DeliveryOutbox.query.filter(
            DeliveryOutbox.status.in_(["PENDING", "FAILED"]),
            DeliveryOutbox.next_attempt_at <= now,
        ).order_by(DeliveryOutbox.next_attempt_at.asc()).limit(batch_size).all()

        filtered = []
        for item in items:
            endpoint = item.partner_endpoint
            if not endpoint.is_active:
                continue
            if endpoint.circuit_until and endpoint.circuit_until > now:
                continue
            item.status = "IN_FLIGHT"
            filtered.append(item)

        db.session.commit()
        return filtered

    def deliver(self, outbox: DeliveryOutbox) -> DeliveryAttempt:
        endpoint = outbox.partner_endpoint
        inbound = outbox.inbound_event
        payload_bytes = inbound.payload.encode()

        signature = self._sign_payload(payload_bytes, endpoint.secret)
        attempt_number = outbox.attempt_count + 1

        start = time.monotonic()
        attempt = DeliveryAttempt(
            delivery_outbox_id=outbox.id,
            attempt_number=attempt_number,
        )

        try:
            resp = self.session.post(
                endpoint.url,
                data=payload_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature-256": signature,
                    "X-Idempotency-Key": str(outbox.inbound_event.id),
                },
                timeout=(self.app.config["DELIVERY_TIMEOUT_CONNECT"], self.app.config["DELIVERY_TIMEOUT_READ"]),
            )
            attempt.status_code = resp.status_code
            attempt.response_body = resp.text[:1024]
            attempt.latency_ms = int((time.monotonic() - start) * 1000)

            if 200 <= resp.status_code < 300:
                outbox.status = "DELIVERED"
                outbox.attempt_count = attempt_number
            else:
                self._handle_failure(outbox, attempt, f"HTTP {resp.status_code}")
        except requests.exceptions.Timeout:
            attempt.latency_ms = int((time.monotonic() - start) * 1000)
            self._handle_failure(outbox, attempt, "timeout")
        except requests.exceptions.ConnectionError as e:
            attempt.latency_ms = int((time.monotonic() - start) * 1000)
            self._handle_failure(outbox, attempt, f"connection_error: {str(e)[:200]}")
        except Exception as e:
            attempt.latency_ms = int((time.monotonic() - start) * 1000)
            self._handle_failure(outbox, attempt, f"error: {str(e)[:200]}")

        db.session.add(attempt)
        db.session.commit()
        return attempt

    def _handle_failure(self, outbox: DeliveryOutbox, attempt: DeliveryAttempt, error_msg: str):
        outbox.attempt_count += 1
        outbox.last_error = error_msg
        attempt.error_message = error_msg

        max_retries = self.app.config["DELIVERY_MAX_RETRIES"]
        if outbox.attempt_count >= max_retries:
            outbox.status = "DEAD_LETTER"
        else:
            outbox.status = "FAILED"
            base_delay = self.app.config["DELIVERY_RETRY_BASE_DELAY"]
            delay = base_delay * (2 ** (outbox.attempt_count - 1)) + random.uniform(0, base_delay * 0.1)
            outbox.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)

    def run_once(self) -> int:
        with self.app.app_context():
            items = self.claim_work(batch_size=self.app.config["DELIVERY_CLAIM_BATCH_SIZE"])
            delivered = 0
            for outbox in items:
                self.deliver(outbox)
                delivered += 1
            return delivered

    def cleanup(self):
        self.session.close()
