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


# ── Database selection ──────────────────────────────────────────────────────
# Priority: SUPABASE_DATABASE_URL (Supabase Postgres) > DATABASE_URL (Render).
# The Render free-tier Postgres (stew-db) is left untouched; the platform now
# runs on Supabase Postgres when SUPABASE_DATABASE_URL is set.
SUPABASE_DB_URL = os.environ.get("SUPABASE_DATABASE_URL", "").strip()
RAW_DATABASE_URL = SUPABASE_DB_URL or settings.DATABASE_URL

ASYNC_DATABASE_URL = _get_async_url(RAW_DATABASE_URL)
IS_SQLITE = "sqlite" in ASYNC_DATABASE_URL
IS_SUPABASE = "supabase" in ASYNC_DATABASE_URL

if SUPABASE_DB_URL:
    logger.info("Database: Supabase Postgres (SUPABASE_DATABASE_URL)")
else:
    logger.info(f"Database: {'SQLite' if IS_SQLITE else 'DATABASE_URL (platform-injected)'}")

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
    # PostgreSQL (production) — hardened connection pool.
    # Fixes: asyncpg.exceptions.ConnectionDoesNotExistError
    # ("connection was closed in the middle of operation").
    # Fixes three failure modes:
    #   1. Stale pooled connections (proxy kills idle conns ~5 min)
    #      -> pool_pre_ping + pool_recycle
    #   2. Render external Postgres / Supabase requiring SSL
    #      -> explicit ssl="require" in connect_args
    #   3. Supabase transaction pooler (port 6543) — no prepared stmt cache
    _pg_connect_args = {
        "timeout": 15,       # asyncpg connect timeout (seconds)
        "command_timeout": 60,
    }
    if "render.com" in ASYNC_DATABASE_URL or os.environ.get("DB_REQUIRE_SSL") == "1":
        _pg_connect_args["ssl"] = "require"
    elif ".pooler.supabase.com" in ASYNC_DATABASE_URL:
        # Supabase supavisor transaction pooler: TLS required,
        # and prepared statements must be disabled
        _pg_connect_args["ssl"] = "require"
        _pg_connect_args["statement_cache_size"] = 0
    elif "supabase.co" in ASYNC_DATABASE_URL:
        _pg_connect_args["ssl"] = "require"
        _pg_connect_args["statement_cache_size"] = 0
    engine = create_async_engine(
        ASYNC_DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,   # verify connection is alive before each checkout
        pool_recycle=240,     # retire connections before proxy idle-timeout kills them
        pool_size=5,          # stay well under free-tier Postgres connection limits
        max_overflow=5,
        pool_timeout=30,      # seconds to wait for a free connection
        connect_args=_pg_connect_args,
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
        # Lightweight migration: add new columns if they don't exist (SQLite)
        if IS_SQLITE:
            await _sqlite_migrate(conn)
        # Supabase: auto-create the persistent-memory tables + storage bucket
        # (same database the REST/storage API uses), so no manual SQL step.
        if IS_SUPABASE:
            await _supabase_bootstrap(conn)


async def _supabase_bootstrap(conn):
    """Create stew_memories / stew_conversations / stew_profiles /
    stew_feature_requests / stew_ad_campaigns and the 'stew-files' storage
    bucket if they don't exist. Safe to run on every startup."""
    from sqlalchemy import text as _text
    stmts = [
        """CREATE TABLE IF NOT EXISTS stew_memories (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            telegram_user_id TEXT NOT NULL,
            memory_type TEXT DEFAULT 'note',
            category TEXT DEFAULT 'general',
            content TEXT NOT NULL,
            importance INTEGER DEFAULT 3,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS stew_conversations (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            telegram_user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tokens INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS stew_profiles (
            telegram_user_id TEXT PRIMARY KEY,
            display_name TEXT,
            username TEXT,
            language TEXT DEFAULT 'en',
            plan TEXT DEFAULT 'free',
            preferred_voice TEXT,
            voice_enabled BOOLEAN DEFAULT FALSE,
            total_messages INTEGER DEFAULT 0,
            monthly_messages INTEGER DEFAULT 0,
            last_message_at TIMESTAMPTZ,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS stew_feature_requests (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            telegram_user_id TEXT NOT NULL,
            feature_text TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            votes INTEGER DEFAULT 0,
            voter_ids TEXT[] DEFAULT '{}',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS stew_ad_campaigns (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            advertiser_name TEXT NOT NULL,
            ad_text TEXT NOT NULL,
            ad_link TEXT,
            button_text TEXT DEFAULT 'Learn More',
            target_audience TEXT DEFAULT 'all',
            frequency INTEGER DEFAULT 5,
            impressions INTEGER DEFAULT 0,
            clicks INTEGER DEFAULT 0,
            budget_impressions INTEGER DEFAULT 10000,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_memories_user ON stew_memories(telegram_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_conversations_user ON stew_conversations(telegram_user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_profiles_user ON stew_profiles(telegram_user_id)",
    ]
    for s in stmts:
        try:
            await conn.execute(_text(s))
        except Exception as e:
            logger.warning(f"Supabase bootstrap statement skipped: {e}")
    # Storage bucket for generated files (stew-files, public)
    try:
        await conn.execute(_text(
            "INSERT INTO storage.buckets (id, name, public) "
            "VALUES ('stew-files', 'stew-files', true) ON CONFLICT DO NOTHING"
        ))
        logger.info("Supabase bootstrap: tables + stew-files bucket ready")
    except Exception as e:
        logger.warning(f"Supabase storage bucket bootstrap skipped: {e}")


async def _sqlite_migrate(conn):
    """Add new columns to existing tables (SQLite doesn't support ALTER ADD COLUMN via create_all)."""
    migrations = [
        ("users", "voice_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
        ("users", "preferred_voice", "VARCHAR(50)"),
        ("users", "response_style", "VARCHAR(20)"),
        ("users", "persona", "VARCHAR(50)"),
        ("users", "custom_instructions", "TEXT"),
        ("users", "persona_name", "VARCHAR(100)"),
        ("users", "language", "VARCHAR(10)"),
        ("users", "preferred_model", "VARCHAR(50)"),
        ("users", "mistral_api_key", "VARCHAR(255)"),
    ]
    for table, column, coltype in migrations:
        try:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
            logger.info(f"Migration: added {table}.{column}")
        except Exception:
            pass  # Column already exists
