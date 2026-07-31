import hashlib
import hmac
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import requests
from sqlalchemy.orm import joinedload

from app.database import db
from app.models import DeliveryAttempt, DeliveryOutbox, PartnerEndpoint

logger = logging.getLogger(__name__)


class DeliveryWorker:
    def __init__(self, app):
        self.app = app
        self.session = requests.Session()
        self._endpoint_semaphores = {}
        self._semaphore_guard = threading.Lock()
        self._setup_session()
        with self.app.app_context():
            db.session.configure(expire_on_commit=False)

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

    def _endpoint_semaphore(self, endpoint: PartnerEndpoint) -> threading.BoundedSemaphore:
        cap = max(int(endpoint.concurrency_cap or 1), 1)
        with self._semaphore_guard:
            semaphore = self._endpoint_semaphores.get(endpoint.id)
            if semaphore is None:
                semaphore = threading.BoundedSemaphore(cap)
                self._endpoint_semaphores[endpoint.id] = semaphore
            return semaphore

    def claim_work(self, batch_size: int = 50) -> list:
        now = datetime.now(UTC)
        items = (
            DeliveryOutbox.query.options(
                joinedload(DeliveryOutbox.partner_endpoint).joinedload(PartnerEndpoint.partner),
                joinedload(DeliveryOutbox.inbound_event),
            )
            .filter(
                DeliveryOutbox.status.in_(["PENDING", "FAILED"]),
                DeliveryOutbox.next_attempt_at <= now,
            )
            .order_by(DeliveryOutbox.next_attempt_at.asc())
            .with_for_update(skip_locked=True)
            .limit(batch_size)
            .all()
        )

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

    def _send(self, outbox: DeliveryOutbox) -> dict:
        endpoint = outbox.partner_endpoint
        inbound = outbox.inbound_event
        payload_bytes = inbound.payload.encode()

        signature = self._sign_payload(payload_bytes, endpoint.secret)
        attempt_number = outbox.attempt_count + 1

        with self._endpoint_semaphore(endpoint):
            start = time.monotonic()
            try:
                resp = self.session.post(
                    endpoint.url,
                    data=payload_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Signature-256": signature,
                        "X-Idempotency-Key": str(inbound.id),
                    },
                    timeout=(
                        self.app.config["DELIVERY_TIMEOUT_CONNECT"],
                        self.app.config["DELIVERY_TIMEOUT_READ"],
                    ),
                )
                return {
                    "status_code": resp.status_code,
                    "response_body": resp.text[:1024],
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "error": None,
                    "attempt_number": attempt_number,
                }
            except requests.exceptions.Timeout:
                return {
                    "status_code": None,
                    "response_body": None,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "error": "timeout",
                    "attempt_number": attempt_number,
                }
            except requests.exceptions.ConnectionError as e:
                return {
                    "status_code": None,
                    "response_body": None,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "error": f"connection_error: {str(e)[:200]}",
                    "attempt_number": attempt_number,
                }
            except requests.RequestException as e:
                return {
                    "status_code": None,
                    "response_body": None,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "error": f"error: {str(e)[:200]}",
                    "attempt_number": attempt_number,
                }

    def _apply(self, outbox: DeliveryOutbox, result: dict) -> DeliveryAttempt:
        attempt = DeliveryAttempt(
            delivery_outbox_id=outbox.id,
            attempt_number=result["attempt_number"],
            status_code=result["status_code"],
            response_body=result["response_body"],
            latency_ms=result["latency_ms"],
            error_message=result["error"],
        )
        if result["error"] is None and 200 <= result["status_code"] < 300:
            outbox.status = "DELIVERED"
            outbox.attempt_count = result["attempt_number"]
        else:
            self._handle_failure(
                outbox, attempt, result["error"] or f"HTTP {result['status_code']}"
            )
        db.session.add(attempt)
        return attempt

    def deliver(self, outbox: DeliveryOutbox) -> DeliveryAttempt:
        with self.app.app_context():
            attempt = self._apply(outbox, self._send(outbox))
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
            delay = base_delay * (2 ** (outbox.attempt_count - 1)) + random.uniform(  # nosec B311
                0, base_delay * 0.1
            )
            outbox.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)

    def run_once(self) -> int:
        with self.app.app_context():
            items = self.claim_work(batch_size=self.app.config["DELIVERY_CLAIM_BATCH_SIZE"])
            if not items:
                return 0
            max_workers = self.app.config["DELIVERY_MAX_CONCURRENCY"]
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                results = list(pool.map(self._send, items))
            for outbox, result in zip(items, results, strict=True):
                self._apply(outbox, result)
            db.session.commit()
            return len(items)

    def cleanup(self):
        self.session.close()
