"""
S.T.E.W Database — SQLAlchemy async engine + session factory.
Supports PostgreSQL (production) and SQLite (dev/testing/free-tier).
"""
import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from server.config import get_settings

settings = get_settings()


def _get_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if "sqlite+aiosqlite" in url:
        return url
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url


ASYNC_DATABASE_URL = _get_async_url(settings.DATABASE_URL)
IS_SQLITE = "sqlite" in ASYNC_DATABASE_URL

engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=settings.DEBUG,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables (used in dev/test; prod uses Alembic)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
