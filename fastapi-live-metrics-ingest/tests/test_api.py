import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.schemas import IngestResponse, PercentileResponse, TopRoutesResponse, TopRouteItem, ErrorRateResponse


@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ingest_success(client, mock_session):
    mock_response = IngestResponse(accepted=2, batch_id=1)
    with patch('app.routers.ingest.IngestService') as mock_class:
        mock_instance = AsyncMock()
        mock_class.return_value = mock_instance
        mock_instance.process_batch = AsyncMock(return_value=mock_response)
        payload = {
            "samples": [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "route_label": "/api/users",
                    "latency_ms": 150.0,
                    "status_code": 200,
                },
                {
                    "timestamp": "2024-01-01T00:00:01Z",
                    "route_label": "/api/orders",
                    "latency_ms": 250.0,
                    "status_code": 201,
                },
            ]
        }
        response = await client.post("/api/v1/ingest", json=payload, headers={"X-Tenant-Id": "1"})
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 2
        assert data["batch_id"] == 1


@pytest.mark.asyncio
async def test_ingest_rejects_empty_batch(client, mock_session):
    payload = {"samples": []}
    response = await client.post("/api/v1/ingest", json=payload, headers={"X-Tenant-Id": "1"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ingest_rejects_oversized_batch(client, mock_session):
    sample = {
        "timestamp": "2024-01-01T00:00:00Z",
        "route_label": "/api/users",
        "latency_ms": 150.0,
        "status_code": 200,
    }
    payload = {"samples": [sample] * 1001}
    response = await client.post("/api/v1/ingest", json=payload, headers={"X-Tenant-Id": "1"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ingest_requires_tenant_header(client):
    payload = {
        "samples": [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "route_label": "/api/users",
                "latency_ms": 150.0,
                "status_code": 200,
            }
        ]
    }
    response = await client.post("/api/v1/ingest", json=payload)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_percentiles_endpoint(client, mock_session):
    mock_response = PercentileResponse(
        tenant_id=1,
        window_start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2024, 1, 1, 0, 5, 0, tzinfo=timezone.utc),
        p50=120.5,
        p95=450.0,
        p99=950.0,
        total_samples=500,
    )
    with patch('app.routers.dashboard.AggregationService') as mock_class:
        mock_instance = AsyncMock()
        mock_class.return_value = mock_instance
        mock_instance.get_percentiles = AsyncMock(return_value=mock_response.model_dump())
        response = await client.get(
            "/api/v1/tenants/1/percentiles",
            params={"window_start": "2024-01-01T00:00:00Z", "window_end": "2024-01-01T00:05:00Z"},
            headers={"X-Tenant-Id": "1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["p50"] == 120.5
        assert data["p95"] == 450.0
        assert data["p99"] == 950.0
        assert data["total_samples"] == 500


@pytest.mark.asyncio
async def test_percentiles_missing_params(client):
    response = await client.get("/api/v1/tenants/1/percentiles", headers={"X-Tenant-Id": "1"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_top_routes_endpoint(client, mock_session):
    mock_rows = [
        {"route_label": "/api/users", "avg_latency_ms": 250.0, "count": 100},
        {"route_label": "/api/orders", "avg_latency_ms": 150.0, "count": 200},
    ]
    with patch('app.routers.dashboard.AggregationService') as mock_class:
        mock_instance = AsyncMock()
        mock_class.return_value = mock_instance
        mock_instance.get_top_routes = AsyncMock(return_value=mock_rows)
        response = await client.get(
            "/api/v1/tenants/1/top-routes",
            params={"window_start": "2024-01-01T00:00:00Z", "window_end": "2024-01-01T00:05:00Z"},
            headers={"X-Tenant-Id": "1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["routes"]) == 2
        assert data["routes"][0]["route_label"] == "/api/users"


@pytest.mark.asyncio
async def test_error_rates_endpoint(client, mock_session):
    mock_data = {"error_count": 5, "total_count": 100, "error_rate": 0.05}
    with patch('app.routers.dashboard.AggregationService') as mock_class:
        mock_instance = AsyncMock()
        mock_class.return_value = mock_instance
        mock_instance.get_error_rates = AsyncMock(return_value=mock_data)
        response = await client.get(
            "/api/v1/tenants/1/error-rates",
            params={"window_start": "2024-01-01T00:00:00Z", "window_end": "2024-01-01T00:05:00Z"},
            headers={"X-Tenant-Id": "1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error_rate"] == 0.05
        assert data["error_count"] == 5


@pytest.mark.asyncio
async def test_request_timing_header(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert "X-Request-Duration-Ms" in response.headers


@pytest.mark.asyncio
async def test_tenant_header_middleware(client):
    response = await client.get("/api/v1/debug/tenant-context", headers={"X-Tenant-Id": "42"})
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == "42"
