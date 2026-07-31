import os
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("DATABASE_URL", "sqlite:///bench_relay.db")

from app import create_app
from app.database import db
from app.models import DeliveryOutbox, InboundEvent, PartnerEndpoint
from app.services.delivery import DeliveryWorker

MOCK_PARTNER_LATENCY_S = 0.2
N_ITEMS = 50
N_REPEATS = 3


class MockPartner(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(MOCK_PARTNER_LATENCY_S)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


def seed_outbox(app, n, round_id):
    with app.app_context():
        endpoint = PartnerEndpoint.query.filter_by(is_active=True).first()
        for i in range(n):
            event = InboundEvent(
                event_type="order.created",
                payload=f'{{"order_id": "{round_id}-{i}"}}',
                idempotency_key=f"dlv-bench-{round_id}-{i}",
            )
            db.session.add(event)
            db.session.flush()
            db.session.add(
                DeliveryOutbox(
                    inbound_event_id=event.id,
                    partner_endpoint_id=endpoint.id,
                    status="PENDING",
                )
            )
        db.session.commit()
        print(f"seeded {n} outbox items -> {endpoint.url}")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8200), MockPartner)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    app = create_app()
    worker = DeliveryWorker(app)

    def run_once():
        with app.app_context():
            return worker.run_once()

    times = []
    for round_id in range(N_REPEATS):
        seed_outbox(app, N_ITEMS, round_id)
        t0 = time.perf_counter()
        run_once()
        times.append(time.perf_counter() - t0)

    print("== Flask delivery worker benchmark (mock partner 200ms) ==")
    print(
        f"run_once({N_ITEMS} items, concurrent) : {statistics.median(times):.2f}s median  "
        f"(per-item ≈ {statistics.median(times) / N_ITEMS * 1000:.0f} ms; partner latency 200ms)"
    )

    with app.app_context():
        item = DeliveryOutbox.query.filter_by(status="PENDING").first()
        if item:
            t0 = time.perf_counter()
            worker.deliver(item)
            print(f"single deliver()                 : {(time.perf_counter() - t0) * 1000:.0f} ms")
    worker.cleanup()
    server.shutdown()


if __name__ == "__main__":
    main()
