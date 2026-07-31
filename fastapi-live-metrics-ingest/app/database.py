from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings


def _connect_args(settings: Settings) -> dict:
    if settings.database_url.startswith("postgresql"):
        return {"server_settings": {"statement_timeout": str(settings.statement_timeout_ms)}}
    return {}


def create_engine(settings: Settings):
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_pre_ping=settings.pool_pre_ping,
        pool_recycle=settings.pool_recycle,
        connect_args=_connect_args(settings),
    )


def create_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
