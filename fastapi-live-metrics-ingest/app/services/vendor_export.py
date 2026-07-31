import asyncio
import logging
import random
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import VendorExportJob, WindowAggregate

logger = logging.getLogger(__name__)

class VendorExportService:
    def __init__(self, session: AsyncSession, settings: Settings, client: httpx.AsyncClient):
        self.session = session
        self.settings = settings
        self.client = client

    async def export_aggregates(
        self, *, tenant_id: int, window_start: datetime, window_end: datetime
    ) -> VendorExportJob:
        job = VendorExportJob(
            tenant_id=tenant_id,
            status="PENDING",
            window_start=window_start,
            window_end=window_end,
            created_at=datetime.now(UTC),
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)

        try:
            stmt = select(WindowAggregate).where(
                WindowAggregate.tenant_id == tenant_id,
                WindowAggregate.window_start >= window_start,
                WindowAggregate.window_end <= window_end,
            )
            result = await self.session.execute(stmt)
            aggregates = result.scalars().all()

            routes = []
            for agg in aggregates:
                routes.append({
                    "route_label": agg.route_label,
                    "p50": agg.p50,
                    "p95": agg.p95,
                    "p99": agg.p99,
                    "sample_count": agg.sample_count,
                    "error_count": agg.error_count,
                })

            payload = {
                "tenant_id": tenant_id,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "routes": routes,
            }

            headers = {"Authorization": f"Bearer {self.settings.vendor_api_key}"}
            url = self.settings.vendor_export_url
            max_attempts = self.settings.vendor_retry_max_attempts
            timeout = self.settings.vendor_timeout_seconds

            last_exception = None
            for attempt in range(max_attempts):
                try:
                    response = await asyncio.wait_for(
                        self.client.post(url, json=payload, headers=headers),
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    last_exception = None
                    break
                except Exception as e:
                    last_exception = e
                    logger.warning(
                        "Vendor export attempt %d/%d failed: %s",
                        attempt + 1,
                        max_attempts,
                        e,
                    )
                    if attempt < max_attempts - 1:
                        delay = (2 ** attempt) + random.uniform(0, 1)  # noqa: S311
                        await asyncio.sleep(delay)

            if last_exception is None:
                job.status = "DONE"
                job.completed_at = datetime.now(UTC)
                job.sample_count = len(routes)
            else:
                job.status = "FAILED"
                job.error_message = str(last_exception)

        except Exception as e:
            job.status = "FAILED"
            job.error_message = str(e)
            logger.error("Vendor export failed: %s", e)

        await self.session.commit()
        await self.session.refresh(job)
        return job
