import asyncio
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings
from app.database import create_engine, create_session_factory
from app.models import MetricSample

logger = logging.getLogger(__name__)

class BackgroundTaskManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._tasks = []
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
                cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.retention_hours)
                async with session_factory() as session:
                    while True:
                        stmt = delete(MetricSample).where(
                            MetricSample.created_at < cutoff
                        ).limit(500)
                        result = await session.execute(stmt)
                        await session.commit()
                        if result.rowcount == 0:
                            break
                        logger.info("Deleted %d old metric samples", result.rowcount)
            except Exception as e:
                logger.error("TTL cleanup error: %s", e)
            await asyncio.sleep(300)

    async def _vendor_export_loop(self, session_factory):
        while not self._shutdown_event.is_set():
            try:
                logger.debug("Vendor export loop tick")
            except Exception as e:
                logger.error("Vendor export loop error: %s", e)
            await asyncio.sleep(60)
