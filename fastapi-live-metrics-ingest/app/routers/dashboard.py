from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session, verify_tenant
from app.schemas import ErrorRateResponse, PercentileResponse, TopRouteItem, TopRoutesResponse
from app.services.aggregation import AggregationService

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}", tags=["dashboard"])


def _parse_datetime(param: str) -> datetime:
    try:
        return datetime.fromisoformat(param)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid datetime: {param}"
        ) from None


@router.get("/percentiles", response_model=PercentileResponse)
async def get_percentiles(
    tenant_id: int = Depends(verify_tenant),
    window_start: str = Query(...),
    window_end: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    ws = _parse_datetime(window_start)
    we = _parse_datetime(window_end)
    svc = AggregationService(session)
    try:
        data = await svc.get_percentiles(tenant_id=tenant_id, window_start=ws, window_end=we)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Aggregation failed",
        ) from None
    return PercentileResponse(
        tenant_id=tenant_id,
        window_start=ws,
        window_end=we,
        p50=data["p50"],
        p95=data["p95"],
        p99=data["p99"],
        total_samples=data["total_samples"],
    )


@router.get("/top-routes", response_model=TopRoutesResponse)
async def get_top_routes(
    tenant_id: int = Depends(verify_tenant),
    window_start: str = Query(...),
    window_end: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    ws = _parse_datetime(window_start)
    we = _parse_datetime(window_end)
    svc = AggregationService(session)
    try:
        rows = await svc.get_top_routes(
            tenant_id=tenant_id, window_start=ws, window_end=we, limit=limit
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Aggregation failed",
        ) from None
    total_samples = sum(r["count"] for r in rows)
    return TopRoutesResponse(
        tenant_id=tenant_id,
        window_start=ws,
        window_end=we,
        routes=[TopRouteItem(**r) for r in rows],
        total_samples=total_samples,
    )


@router.get("/error-rates", response_model=ErrorRateResponse)
async def get_error_rates(
    tenant_id: int = Depends(verify_tenant),
    window_start: str = Query(...),
    window_end: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    ws = _parse_datetime(window_start)
    we = _parse_datetime(window_end)
    svc = AggregationService(session)
    try:
        data = await svc.get_error_rates(tenant_id=tenant_id, window_start=ws, window_end=we)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Aggregation failed",
        ) from None
    return ErrorRateResponse(
        tenant_id=tenant_id,
        window_start=ws,
        window_end=we,
        error_count=data["error_count"],
        total_count=data["total_count"],
        error_rate=data["error_rate"],
    )
