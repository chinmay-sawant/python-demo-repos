import pytest
from app.database import db
from app.models import InboundEvent


@pytest.mark.usefixtures("sample_partner")
class TestIngestRoundTrip:
    @pytest.mark.parametrize("body", [
        b'{"event_type": "nested", "payload": {"nested": {"list": [1, 2, {"k": "v"}]}}, "idempotency_key": "rt-1"}',
        b'{"event_type": "list.payload", "payload": [1, "two", {"three": 3}], "idempotency_key": "rt-2"}',
        b'{"event_type": "scalar.int", "payload": 42, "idempotency_key": "rt-3"}',
        b'{"event_type": "scalar.str", "payload": "hello", "idempotency_key": "rt-4"}',
        b'{"event_type": "unicode", "payload": {"emoji": "\\ud83d\\ude80"}, "idempotency_key": "rt-5"}',
        b'\n{\n  "event_type": "whitespace",\n  "payload": {"a": 1}\n}\n',
    ])
    def test_stored_bytes_identical_to_received(self, client, body):
        resp = client.post(
            "/api/v1/webhooks",
            data=body,
            content_type="application/json",
            headers={"X-Api-Key": "test-api-key"},
        )
        assert resp.status_code == 201
        event = db.session.get(InboundEvent, resp.get_json()["event_id"])
        assert event.payload.encode() == body

    def test_ingest_parses_once_and_stores_raw(self, client, app):
        body = b'{"event_type": "order.created", "payload": {"order_id": "x"}, "idempotency_key": "rt-parse"}'
        resp = client.post(
            "/api/v1/webhooks",
            data=body,
            content_type="application/json",
            headers={"X-Api-Key": "test-api-key"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["fan_out"] == 1
        event = db.session.get(InboundEvent, data["event_id"])
        assert event.event_type == "order.created"
        assert event.payload.encode() == body
