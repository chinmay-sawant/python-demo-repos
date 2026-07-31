import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import IngestBatch, MetricSample
from app.schemas import IngestRequest, IngestResponse

_numeric_segment_re = re.compile(r"/\d+(/|$)")


def normalize_route(label: str) -> str:
    return _numeric_segment_re.sub(r"/{id}\1", label.split("?", 1)[0])


class IngestService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def process_batch(self, *, tenant_id: int, request: IngestRequest) -> IngestResponse:
        if request.idempotency_key:
            stmt = (
                pg_insert(IngestBatch)
                .values(
                    tenant_id=tenant_id,
                    idempotency_key=request.idempotency_key,
                    sample_count=len(request.samples),
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(IngestBatch.id, IngestBatch.sample_count)
            )
            row = (await self.session.execute(stmt)).first()
            if row is None:
                existing = await self._find_batch_by_key(request.idempotency_key)
                if existing is None:
                    raise RuntimeError("Idempotency conflict but no existing batch found")
                return IngestResponse(
                    accepted=existing.sample_count,
                    batch_id=existing.id,
                    already_processed=True,
                )
            batch_id = row.id
        else:
            batch = IngestBatch(
                tenant_id=tenant_id,
                idempotency_key=None,
                sample_count=len(request.samples),
            )
            self.session.add(batch)
            try:
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                existing = await self._find_batch_by_key(request.idempotency_key or "")
                if existing:
                    return IngestResponse(
                        accepted=existing.sample_count,
                        batch_id=existing.id,
                        already_processed=True,
                    )
                raise
            batch_id = batch.id

        samples = [
            MetricSample(
                tenant_id=tenant_id,
                batch_id=batch_id,
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
            batch_id=batch_id,
            already_processed=False,
        )

    async def _find_batch_by_key(self, idempotency_key: str) -> IngestBatch | None:
        stmt = select(IngestBatch).where(IngestBatch.idempotency_key == idempotency_key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
