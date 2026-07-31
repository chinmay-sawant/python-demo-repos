from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetricSample


def _percentile_query(*, tenant_id: int, window_start: datetime, window_end: datetime):
    return select(
        func.percentile_cont(0.50).within_group(MetricSample.latency_ms.asc()).label("p50"),
        func.percentile_cont(0.95).within_group(MetricSample.latency_ms.asc()).label("p95"),
        func.percentile_cont(0.99).within_group(MetricSample.latency_ms.asc()).label("p99"),
        func.count().label("total_samples"),
    ).where(
        MetricSample.tenant_id == tenant_id,
        MetricSample.timestamp >= window_start,
        MetricSample.timestamp < window_end,
    )


class AggregationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _is_postgres(self) -> bool:
        bind = getattr(self.session, "bind", None)
        if bind is None:
            return False
        engine = getattr(bind, "sync_engine", bind)
        dialect = getattr(engine, "dialect", None)
        return dialect is not None and dialect.name == "postgresql"

    async def get_percentiles(
        self, *, tenant_id: int, window_start: datetime, window_end: datetime
    ) -> dict:
        if self._is_postgres():
            query = _percentile_query(
                tenant_id=tenant_id, window_start=window_start, window_end=window_end
            )
            row = (await self.session.execute(query)).one()
            return {
                "p50": round(row.p50, 2) if row.p50 is not None else None,
                "p95": round(row.p95, 2) if row.p95 is not None else None,
                "p99": round(row.p99, 2) if row.p99 is not None else None,
                "total_samples": row.total_samples,
            }
        query = select(MetricSample.latency_ms).where(
            MetricSample.tenant_id == tenant_id,
            MetricSample.timestamp >= window_start,
            MetricSample.timestamp < window_end,
        )
        result = await self.session.execute(query)
        rows = result.scalars().all()
        total_samples = len(rows)
        if total_samples == 0:
            return {"p50": None, "p95": None, "p99": None, "total_samples": 0}
        sorted_vals = sorted(rows)
        p50 = sorted_vals[total_samples // 2]
        p95 = sorted_vals[int(total_samples * 0.95)]
        p99 = sorted_vals[int(total_samples * 0.99)]
        return {
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "total_samples": total_samples,
        }

    async def get_top_routes(
        self,
        *,
        tenant_id: int,
        window_start: datetime,
        window_end: datetime,
        limit: int = 10,
    ) -> list[dict]:
        query = (
            select(
                MetricSample.route_label,
                func.avg(MetricSample.latency_ms).label("avg_latency_ms"),
                func.count().label("count"),
            )
            .where(
                MetricSample.tenant_id == tenant_id,
                MetricSample.timestamp >= window_start,
                MetricSample.timestamp < window_end,
            )
            .group_by(MetricSample.route_label)
            .order_by(func.avg(MetricSample.latency_ms).desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [
            {
                "route_label": row.route_label,
                "avg_latency_ms": round(row.avg_latency_ms, 2),
                "count": row.count,
            }
            for row in result
        ]

    async def get_error_rates(
        self, *, tenant_id: int, window_start: datetime, window_end: datetime
    ) -> dict:
        query = select(
            func.count().label("total_count"),
            func.sum(case((MetricSample.status_code >= 400, 1), else_=0)).label("error_count"),
        ).where(
            MetricSample.tenant_id == tenant_id,
            MetricSample.timestamp >= window_start,
            MetricSample.timestamp < window_end,
        )
        result = await self.session.execute(query)
        row = result.one()
        total_count = row.total_count or 0
        error_count = row.error_count or 0
        error_rate = round(error_count / total_count, 4) if total_count > 0 else 0.0
        return {"error_count": error_count, "total_count": total_count, "error_rate": error_rate}
