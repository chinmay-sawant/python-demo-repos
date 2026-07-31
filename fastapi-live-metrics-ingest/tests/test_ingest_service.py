import asyncio
import re
from datetime import UTC

import pytest
from app.config import Settings
from app.models import Base, IngestBatch, MetricSample, Tenant
from app.schemas import IngestRequest, MetricSampleIn
from app.services.ingest import IngestService, normalize_route
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_old_route_cleanup_re = re.compile(r"\?.*$")
_old_numeric_segment_re = re.compile(r"/\d+(/|$)")


def _old_normalize_route(label: str) -> str:
    label = _old_route_cleanup_re.sub("", label)
    # intentionally-old reference implementation; kept to prove single-regex equivalence
    label = _old_numeric_segment_re.sub(r"/{id}\1", label)  # goslop-ignore: PERF-PY-18
    return label


CASES = [
    "/api/users",
    "/api/orders/5",
    "/api/orders/5/items/3",
    "/api/orders/5?trace=1",
    "/api/orders/5/items/3?trace=42",
    "/api/orders/123?page=2",
    "/api/orders?x=/5",
    "/?/5",
    "/5",
    "/5?",
    "/a/5/6",
    "/api/v1.5/items?x=1",
    "/api/orders/5?trace=1/2",
    "/api/orders/005/items/007",
    "/api/customers/1/orders?trace=9",
    "/no-digits-here",
    "/api//5",
    "/api/orders/5/",
]


@pytest.mark.parametrize("label", CASES)
def test_normalize_route_matches_legacy_two_pass(label):
    assert normalize_route(label) == _old_normalize_route(label)


def test_normalize_route_query_string_dropped():
    assert normalize_route("/api/orders?trace=abc") == "/api/orders"


def test_normalize_route_numeric_segments():
    assert normalize_route("/api/orders/5") == "/api/orders/{id}"
    assert normalize_route("/api/orders/5/items/3") == "/api/orders/{id}/items/{id}"


def test_normalize_route_numeric_segment_before_query():
    assert normalize_route("/api/orders/5?trace=1") == "/api/orders/{id}"


async def _make_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _sample(route_label="/api/orders/1/items/2", latency_ms=10.0):
    from datetime import datetime

    return MetricSampleIn(
        route_label=route_label,
        latency_ms=latency_ms,
        status_code=200,
        ua_class=None,
        timestamp=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_concurrent_idempotent_batches_single_batch(tmp_path):
    engine, factory = await _make_factory(tmp_path)
    try:
        async with factory() as session:
            session.add(Tenant(name="t1"))
            await session.commit()

        async def submit(i):
            async with factory() as session:
                service = IngestService(session, Settings())
                request = IngestRequest(
                    idempotency_key="same-key", samples=[_sample(route_label=f"/api/orders/{i}")]
                )
                return await service.process_batch(tenant_id=1, request=request)

        results = await asyncio.gather(*[submit(i) for i in range(10)])

        winners = [r for r in results if not r.already_processed]
        assert len(winners) == 1
        assert all(r.batch_id == winners[0].batch_id for r in results)
        assert all(r.accepted == 1 for r in results)

        async with factory() as session:
            batches = (await session.execute(select(IngestBatch))).scalars().all()
            assert len(batches) == 1
            samples = (await session.execute(select(MetricSample))).scalars().all()
            assert len(samples) == 1
            assert samples[0].batch_id == batches[0].id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sequential_duplicate_returns_existing_batch(tmp_path):
    engine, factory = await _make_factory(tmp_path)
    try:
        async with factory() as session:
            session.add(Tenant(name="t1"))
            await session.commit()

        async with factory() as session:
            service = IngestService(session, Settings())
            request = IngestRequest(idempotency_key="dup-key", samples=[_sample()])
            first = await service.process_batch(tenant_id=1, request=request)
            second = await service.process_batch(tenant_id=1, request=request)

        assert not first.already_processed
        assert second.already_processed
        assert second.batch_id == first.batch_id

        async with factory() as session:
            batches = (await session.execute(select(IngestBatch))).scalars().all()
            assert len(batches) == 1
    finally:
        await engine.dispose()
