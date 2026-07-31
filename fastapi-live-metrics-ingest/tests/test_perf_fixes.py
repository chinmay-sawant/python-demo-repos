from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.config import Settings
from app.database import _connect_args, create_engine
from app.models import Base, IngestBatch, MetricSample, Tenant, VendorExportJob
from app.services.aggregation import AggregationService, _percentile_query
from app.tasks import BackgroundTaskManager
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def _make_factory(tmp_path, name):
    db_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def test_fa1_percentile_query_compiles_percentile_cont():
    window_start = datetime(2024, 1, 1, tzinfo=UTC)
    window_end = datetime(2024, 1, 1, 1, 0, 0, tzinfo=UTC)
    query = _percentile_query(tenant_id=1, window_start=window_start, window_end=window_end)
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "percentile_cont" in sql
    assert "within group" in sql.lower()
    assert "metric_samples" in sql


def test_fa1_dialect_gating():
    pg_session = MagicMock()
    pg_session.bind.sync_engine.dialect.name = "postgresql"
    assert AggregationService(pg_session)._is_postgres() is True
    sqlite_session = MagicMock()
    sqlite_session.bind.sync_engine.dialect.name = "sqlite"
    assert AggregationService(sqlite_session)._is_postgres() is False
    assert AggregationService(MagicMock())._is_postgres() is False


@pytest.mark.asyncio
async def test_fa1_postgres_branch_returns_sql_row_shape():
    session = AsyncMock()
    session.bind.sync_engine.dialect.name = "postgresql"
    row = SimpleNamespace(p50=123.456, p95=500.123, p99=999.999, total_samples=10)
    session.execute.return_value = MagicMock()
    session.execute.return_value.one.return_value = row
    result = await AggregationService(session).get_percentiles(
        tenant_id=1,
        window_start=datetime(2024, 1, 1, tzinfo=UTC),
        window_end=datetime(2024, 1, 2, tzinfo=UTC),
    )
    assert result == {"p50": 123.46, "p95": 500.12, "p99": 1000.0, "total_samples": 10}
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_fa1_sqlite_fallback_sorts_app_side(tmp_path):
    engine, factory = await _make_factory(tmp_path, "fa1.db")
    base = datetime(2024, 1, 1, tzinfo=UTC)
    async with factory() as session:
        tenant = Tenant(name="t1")
        session.add(tenant)
        await session.flush()
        batch = IngestBatch(tenant_id=tenant.id, sample_count=4)
        session.add(batch)
        await session.flush()
        for i, v in enumerate([100.0, 200.0, 300.0, 400.0]):
            session.add(MetricSample(
                tenant_id=tenant.id,
                batch_id=batch.id,
                route_label="/api/x",
                latency_ms=v,
                status_code=200,
                ua_class="t",
                timestamp=base + timedelta(seconds=i),
                created_at=base,
            ))
        await session.commit()
        result = await AggregationService(session).get_percentiles(
            tenant_id=tenant.id,
            window_start=base,
            window_end=base + timedelta(seconds=60),
        )
        assert result == {"p50": 300.0, "p95": 400.0, "p99": 400.0, "total_samples": 4}
    await engine.dispose()


@pytest.mark.asyncio
async def test_fa1_sqlite_fallback_empty_window_shape(tmp_path):
    engine, factory = await _make_factory(tmp_path, "fa1_empty.db")
    base = datetime(2024, 1, 1, tzinfo=UTC)
    async with factory() as session:
        result = await AggregationService(session).get_percentiles(
            tenant_id=1,
            window_start=base,
            window_end=base + timedelta(hours=1),
        )
        assert result == {"p50": None, "p95": None, "p99": None, "total_samples": 0}
    await engine.dispose()


def test_fa5_engine_created_with_configured_pool_options(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}",
        pool_size=4,
        max_overflow=2,
        pool_timeout=7,
        pool_pre_ping=True,
        pool_recycle=1234,
    )
    engine = create_engine(settings)
    pool = engine.sync_engine.pool
    assert pool.size() == 4
    assert pool._max_overflow == 2
    assert pool._timeout == 7
    assert pool._pre_ping is True
    assert pool._recycle == 1234
    engine.sync_engine.dispose()


def test_fa5_connect_args_gated_by_driver():
    pg = _connect_args(Settings(database_url="postgresql+asyncpg://u:p@h/db", statement_timeout_ms=30000))
    assert pg == {"server_settings": {"statement_timeout": "30000"}}
    sq = _connect_args(Settings(database_url="sqlite+aiosqlite:///x.db", statement_timeout_ms=30000))
    assert sq == {}


@pytest.mark.asyncio
async def test_fa8_export_tick_marks_pending_job_done(tmp_path):
    engine, factory = await _make_factory(tmp_path, "fa8.db")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'fa8.db'}",
        vendor_export_url="https://vendor.example/export",
        vendor_api_key="sekret",
        vendor_retry_max_attempts=1,
        vendor_timeout_seconds=5,
    )
    manager = BackgroundTaskManager(settings)
    now = datetime.now(UTC)
    async with factory() as session:
        tenant = Tenant(name="t1")
        session.add(tenant)
        await session.flush()
        job = VendorExportJob(
            tenant_id=tenant.id,
            status="PENDING",
            window_start=now - timedelta(hours=2),
            window_end=now - timedelta(hours=1),
            created_at=now,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    client_mock = AsyncMock()
    client_mock.post.return_value = SimpleNamespace(status_code=200, raise_for_status=lambda: None)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("app.tasks.httpx.AsyncClient", return_value=cm):
        await manager._run_vendor_export_tick(factory)

    async with factory() as session:
        job = (await session.execute(select(VendorExportJob).where(VendorExportJob.id == job_id))).scalar_one()
        assert job.status == "DONE"
        assert job.completed_at is not None
        assert job.sample_count == 0
        assert job.error_message is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_fa8_export_tick_marks_pending_job_failed(tmp_path):
    engine, factory = await _make_factory(tmp_path, "fa8_fail.db")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'fa8_fail.db'}",
        vendor_export_url="https://vendor.example/export",
        vendor_api_key="sekret",
        vendor_retry_max_attempts=1,
        vendor_timeout_seconds=5,
    )
    manager = BackgroundTaskManager(settings)
    now = datetime.now(UTC)
    async with factory() as session:
        tenant = Tenant(name="t1")
        session.add(tenant)
        await session.flush()
        job = VendorExportJob(
            tenant_id=tenant.id,
            status="PENDING",
            window_start=now - timedelta(hours=2),
            window_end=now - timedelta(hours=1),
            created_at=now,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    client_mock = AsyncMock()
    client_mock.post.side_effect = httpx.ConnectError("boom")
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("app.tasks.httpx.AsyncClient", return_value=cm):
        await manager._run_vendor_export_tick(factory)

    async with factory() as session:
        job = (await session.execute(select(VendorExportJob).where(VendorExportJob.id == job_id))).scalar_one()
        assert job.status == "FAILED"
        assert job.completed_at is None
        assert job.error_message == "boom"
    await engine.dispose()
