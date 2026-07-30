from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime

class MetricSampleIn(BaseModel):
    route_label: str = Field(..., max_length=512)
    latency_ms: float = Field(..., ge=0)
    status_code: int = Field(..., ge=100, lt=600)
    ua_class: Optional[str] = None
    timestamp: datetime

class IngestRequest(BaseModel):
    samples: List[MetricSampleIn] = Field(..., max_length=1000)
    idempotency_key: Optional[str] = None

    @field_validator("samples")
    @classmethod
    def check_not_empty(cls, v):
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
    p50: Optional[float] = None
    p95: Optional[float] = None
    p99: Optional[float] = None
    total_samples: int = 0

class TopRouteItem(BaseModel):
    route_label: str
    avg_latency_ms: float
    count: int

class TopRoutesResponse(BaseModel):
    tenant_id: int
    window_start: datetime
    window_end: datetime
    routes: List[TopRouteItem]
    total_samples: int = 0

class ErrorRateResponse(BaseModel):
    tenant_id: int
    window_start: datetime
    window_end: datetime
    error_count: int
    total_count: int
    error_rate: float = 0.0
