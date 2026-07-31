from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class MetricSampleIn(BaseModel):
    route_label: str = Field(..., max_length=512)
    latency_ms: float = Field(..., ge=0)
    status_code: int = Field(..., ge=100, lt=600)
    ua_class: str | None = None
    timestamp: datetime


class IngestRequest(BaseModel):
    samples: list[MetricSampleIn] = Field(..., max_length=1000)
    idempotency_key: str | None = None

    @field_validator("samples")
    @classmethod
    def check_not_empty(cls, v):  # noqa: V107
        if not v:
            raise ValueError("At least one sample is required")
        return v


class IngestResponse(BaseModel):
    accepted: int
    batch_id: int
    already_processed: bool = False


class PercentileResponse(BaseModel):
    tenant_id: int
    window_start: datetime
    window_end: datetime
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    total_samples: int = 0


class TopRouteItem(BaseModel):
    route_label: str
    avg_latency_ms: float
    count: int


class TopRoutesResponse(BaseModel):
    tenant_id: int
    window_start: datetime
    window_end: datetime
    routes: list[TopRouteItem]
    total_samples: int = 0


class ErrorRateResponse(BaseModel):
    tenant_id: int
    window_start: datetime
    window_end: datetime
    error_count: int
    total_count: int
    error_rate: float = 0.0
