"""
S.T.E.W Database — SQLAlchemy async engine + session factory.
Supports PostgreSQL (production) and SQLite (dev/testing/free-tier).
"""
import os
import logging
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text

from server.config import get_settings

logger = logging.getLogger(__name__)
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

# For SQLite, use StaticPool to share a single connection (avoids "database is locked")
if IS_SQLITE:
    from sqlalchemy.pool import StaticPool
    engine = create_async_engine(
        ASYNC_DATABASE_URL,
        echo=settings.DEBUG,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
else:
    engine = create_async_engine(
        ASYNC_DATABASE_URL,
        echo=settings.DEBUG,
    )

# Set PRAGMA on every new SQLite connection via event listener (outside transactions)
if IS_SQLITE:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
        logger.info("SQLite connection: WAL mode + 30s busy timeout set")

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
        if IS_SQLITE:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=30000"))
            logger.info("SQLite WAL mode enabled with 30s busy timeout")
        await conn.run_sync(Base.metadata.create_all)
