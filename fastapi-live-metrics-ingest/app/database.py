from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.config import Settings

def create_engine(settings: Settings):
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_pre_ping=settings.pool_pre_ping,
    )

def create_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
