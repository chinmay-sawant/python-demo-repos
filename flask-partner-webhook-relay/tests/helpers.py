import threading
import time
from http.server import BaseHTTPRequestHandler

from app.database import db
from app.models import DeliveryOutbox, InboundEvent, Partner, PartnerEndpoint
from sqlalchemy import event


class MockPartnerHandler(BaseHTTPRequestHandler):
    latency = 0.05
    concurrent = 0
    max_concurrent = 0
    lock = threading.Lock()

    def do_POST(self):
        with self.lock:
            self.__class__.concurrent += 1
            self.__class__.max_concurrent = max(
                self.__class__.max_concurrent, self.__class__.concurrent
            )
        time.sleep(self.__class__.latency)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
        with self.lock:
            self.__class__.concurrent -= 1

    @classmethod
    def reset(cls):
        cls.concurrent = 0
        cls.max_concurrent = 0

    def log_message(self, *args):
        pass


def seed_outbox(app, url, n, *, cap=10, latency=0.05, status="PENDING"):
    MockPartnerHandler.reset()
    MockPartnerHandler.latency = latency
    with app.app_context():
        partner = Partner(name="Test Partner", api_key_hash="hash", is_active=True)
        db.session.add(partner)
        db.session.flush()
        endpoint = PartnerEndpoint(
            partner_id=partner.id,
            url=url,
            secret="secret",
            is_active=True,
            concurrency_cap=cap,
        )
        db.session.add(endpoint)
        db.session.flush()
        for i in range(n):
            event = InboundEvent(
                event_type="order.created",
                payload=f'{{"order_id": "{i}"}}',
                idempotency_key=f"seed-{i}",
            )
            db.session.add(event)
            db.session.flush()
            db.session.add(
                DeliveryOutbox(
                    inbound_event_id=event.id,
                    partner_endpoint_id=endpoint.id,
                    status=status,
                )
            )
        db.session.commit()
        return endpoint.id


def count_selects(engine):
    counter = {"selects": 0}

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["selects"] += 1

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    return counter, _before_cursor_execute
