import asyncio
import random
from datetime import UTC, datetime, timedelta

from app.database import create_session_factory
from app.models import Base, MetricSample, Tenant
from sqlalchemy import insert

DB_URL = "sqlite+aiosqlite:///bench_metrics.db"
SAMPLE_ROWS = 200_000


async def main():
    engine = create_engine_from_url(DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    now = datetime.now(UTC)
    async with factory() as session:
        tenant = Tenant(name="bench-tenant")
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
        await session.commit()

    async with factory() as session:
        chunk = 10_000
        routes = [f"/api/orders/{i % 500}/items/{i % 20}" for i in range(1000)]
        base = now - timedelta(hours=24)
        for start in range(0, SAMPLE_ROWS, chunk):
            rows = [
                {
                    "tenant_id": tenant_id,
                    "batch_id": 0,
                    "route_label": routes[random.randrange(len(routes))],
                    "latency_ms": random.expovariate(1 / 120),
                    "status_code": random.choice([200, 200, 200, 200, 500]),
                    "ua_class": "bench",
                    "timestamp": base + timedelta(seconds=random.randrange(86_400)),
                    "created_at": now,
                }
                for _ in range(chunk)
            ]
            await session.execute(insert(MetricSample), rows)
        await session.commit()

    print(f"seeded {SAMPLE_ROWS} metric samples for tenant_id={tenant_id}")
    await engine.dispose()


def create_engine_from_url(url):
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(url)


if __name__ == "__main__":
    asyncio.run(main())
