import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.database import create_engine, create_session_factory
from app.models import MetricSample, VendorExportJob
from app.services.vendor_export import VendorExportService

logger = logging.getLogger(__name__)


class BackgroundTaskManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._tasks: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()

    async def start(self):
        engine = create_engine(self.settings)
        session_factory = create_session_factory(engine)
        self._tasks = [
            asyncio.create_task(self._ttl_cleanup_loop(session_factory)),
            asyncio.create_task(self._vendor_export_loop(session_factory)),
        ]

    async def stop(self):
        self._shutdown_event.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _ttl_cleanup_loop(self, session_factory):
        while not self._shutdown_event.is_set():
            try:
                cutoff = datetime.now(UTC) - timedelta(hours=self.settings.retention_hours)
                async with session_factory() as session:
                    while True:
                        stmt = delete(MetricSample).where(
                            MetricSample.id.in_(
                                select(MetricSample.id)
                                .where(MetricSample.created_at < cutoff)
                                .limit(500)
                            )
                        )
                        result = await session.execute(stmt)
                        await session.commit()
                        if result.rowcount == 0:
                            break
                        logger.info("Deleted %d old metric samples", result.rowcount)
            except SQLAlchemyError as e:
                logger.error("TTL cleanup error: %s", e)
            await asyncio.sleep(300)

    async def _vendor_export_loop(self, session_factory):
        while not self._shutdown_event.is_set():
            try:
                await self._run_vendor_export_tick(session_factory)
            except (SQLAlchemyError, httpx.HTTPError) as e:
                logger.error("Vendor export loop error: %s", e)
            await asyncio.sleep(60)

    async def _run_vendor_export_tick(self, session_factory):
        async with (
            session_factory() as session,
            httpx.AsyncClient(timeout=self.settings.vendor_timeout_seconds) as client,
        ):
            service = VendorExportService(session, self.settings, client)
            now = datetime.now(UTC)
            stmt = (
                select(VendorExportJob)
                .where(
                    VendorExportJob.status == "PENDING",
                    VendorExportJob.window_end <= now,
                )
                .order_by(VendorExportJob.created_at.asc())
                .limit(self.settings.vendor_max_concurrency)
            )
            result = await session.execute(stmt)
            for job in result.scalars().all():
                job_id = job.id
                exported = await service.export_aggregates(
                    tenant_id=job.tenant_id,
                    window_start=job.window_start,
                    window_end=job.window_end,
                )
                await session.execute(
                    update(VendorExportJob)
                    .where(VendorExportJob.id == job_id)
                    .values(
                        status=exported.status,
                        completed_at=exported.completed_at,
                        sample_count=exported.sample_count,
                        error_message=exported.error_message,
                    )
                )
            await session.commit()
