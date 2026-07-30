import json
import pytest
from app.models import DeliveryOutbox


class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}


class TestIngestAuth:
    def test_ingest_requires_auth(self, client):
        resp = client.post("/api/v1/webhooks", json={})
        assert resp.status_code == 401


@pytest.mark.usefixtures("sample_partner")
class TestIngestSuccess:
    def test_ingest_success(self, client):
        payload = {
            "event_type": "order.created",
            "payload": {"order_id": "123"},
            "idempotency_key": "key-001",
        }
        resp = client.post(
            "/api/v1/webhooks",
            json=payload,
            headers={"X-Api-Key": "test-api-key"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "event_id" in data
        assert data["fan_out"] == 1
        assert data["duplicate"] is False


class TestIngestValidation:
    def test_ingest_rejects_non_json(self, client):
        resp = client.post(
            "/api/v1/webhooks",
            data="not json",
            content_type="text/plain",
            headers={"X-Api-Key": "test-api-key"},
        )
        assert resp.status_code == 415

    def test_ingest_rejects_large_payload(self, client):
        big_body = "x" * (300 * 1024)
        resp = client.post(
            "/api/v1/webhooks",
            data=big_body,
            content_type="application/json",
            headers={"X-Api-Key": "test-api-key"},
        )
        assert resp.status_code == 413

    def test_ingest_rejects_missing_event_type(self, client):
        resp = client.post(
            "/api/v1/webhooks",
            json={"payload": {"a": 1}},
            headers={"X-Api-Key": "test-api-key"},
        )
        assert resp.status_code == 400


@pytest.mark.usefixtures("sample_partner")
class TestIngestIdempotency:
    def test_ingest_idempotency(self, client):
        payload = {
            "event_type": "order.shipped",
            "payload": {"order_id": "456"},
            "idempotency_key": "dup-key-001",
        }
        headers = {"X-Api-Key": "test-api-key"}
        resp1 = client.post("/api/v1/webhooks", json=payload, headers=headers)
        assert resp1.status_code == 201
        data1 = resp1.get_json()
        assert data1["duplicate"] is False

        resp2 = client.post("/api/v1/webhooks", json=payload, headers=headers)
        assert resp2.status_code == 200
        data2 = resp2.get_json()
        assert data2["duplicate"] is True
        assert data2["event_id"] == data1["event_id"]


@pytest.mark.xfail(reason="queue-depth CLI command not yet implemented", strict=True)
class TestCli:
    def test_queue_depth_command(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["queue-depth"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "queue_depth" in data
