import re

from functools import partial
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.schemas import IngestRequest, IngestResponse
from app.models import IngestBatch, MetricSample

_route_cleanup_re = re.compile(r"\?.*$")
_numeric_segment_re = re.compile(r"/\d+(/|$)")

def normalize_route(label: str) -> str:
    label = _route_cleanup_re.sub("", label)
    label = _numeric_segment_re.sub(r"/{id}\1", label)
    return label

class IngestService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def process_batch(self, *, tenant_id: int, request: IngestRequest) -> IngestResponse:
        if request.idempotency_key:
            existing = await self._find_batch_by_key(request.idempotency_key)
            if existing:
                return IngestResponse(
                    accepted=existing.sample_count,
                    batch_id=existing.id,
                    already_processed=True,
                )

        batch = IngestBatch(
            tenant_id=tenant_id,
            idempotency_key=request.idempotency_key,
            sample_count=len(request.samples),
        )
        self.session.add(batch)

        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self._find_batch_by_key(request.idempotency_key)
            if existing:
                return IngestResponse(
                    accepted=existing.sample_count,
                    batch_id=existing.id,
                    already_processed=True,
                )
            raise

        samples = [
            MetricSample(
                tenant_id=tenant_id,
                batch_id=batch.id,
                route_label=normalize_route(s.route_label),
                latency_ms=s.latency_ms,
                status_code=s.status_code,
                ua_class=s.ua_class if s.ua_class else "unknown",
                timestamp=s.timestamp,
            )
            for s in request.samples
        ]
        self.session.add_all(samples)
        await self.session.commit()

        return IngestResponse(
            accepted=len(samples),
            batch_id=batch.id,
            already_processed=False,
        )

    async def _find_batch_by_key(self, idempotency_key: str) -> Optional[IngestBatch]:
        stmt = select(IngestBatch).where(IngestBatch.idempotency_key == idempotency_key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
