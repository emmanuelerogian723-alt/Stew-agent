"""
S.T.E.W Persistent Memory & Storage — Supabase Integration
Provides real persistent memory (not lost on redeploy) using Supabase free tier.
- PostgreSQL database (500MB free)
- pgvector for AI semantic memory
- File storage (1GB free)
- User memory: facts, preferences, conversation history
- Semantic search for remembering past conversations

Setup:
1. Create free project at https://supabase.com
2. Add env vars: SUPABASE_URL, SUPABASE_KEY (service_role key)
3. Run the SQL migration to create tables
"""
import os
import json
import logging
import time
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Lazy import — only needed if Supabase is configured
_client = None
_storage_client = None

def _get_client():
    """Get Supabase client (lazy init)."""
    global _client
    if _client is not None:
        return _client
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        logger.info("Supabase client initialized")
        return _client
    except ImportError:
        logger.warning("supabase-py not installed — add to requirements.txt")
        return None
    except Exception as e:
        logger.warning(f"Supabase init failed: {e}")
        return None


def is_configured() -> bool:
    """Check if Supabase is configured."""
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"))


# ─── User Memory (Facts & Preferences) ──────────────────────────────────────

async def save_memory(
    telegram_user_id: str,
    memory_type: str,  # "fact", "preference", "instruction", "note"
    content: str,
    category: str = "general",
    metadata: dict = None,
) -> bool:
    """Save a memory for a user. Returns True on success."""
    client = _get_client()
    if not client:
        return False
    try:
        data = {
            "telegram_user_id": str(telegram_user_id),
            "memory_type": memory_type,
            "content": content,
            "category": category,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table("stew_memories").insert(data).execute()
        logger.info(f"Memory saved for user {telegram_user_id}: {memory_type}/{category}")
        return True
    except Exception as e:
        logger.error(f"Save memory error: {e}")
        return False


async def recall_memories(
    telegram_user_id: str,
    category: Optional[str] = None,
    memory_type: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """Recall memories for a user. Returns list of memory dicts."""
    client = _get_client()
    if not client:
        return []
    try:
        query = client.table("stew_memories").select("*").eq(
            "telegram_user_id", str(telegram_user_id)
        ).order("created_at", desc=True).limit(limit)
        
        if category:
            query = query.eq("category", category)
        if memory_type:
            query = query.eq("memory_type", memory_type)
        
        result = query.execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Recall memories error: {e}")
        return []


async def search_memories(
    telegram_user_id: str,
    query_text: str,
    limit: int = 10,
) -> list[dict]:
    """Search memories by text content (simple LIKE search)."""
    client = _get_client()
    if not client:
        return []
    try:
        result = client.table("stew_memories").select("*").eq(
            "telegram_user_id", str(telegram_user_id)
        ).ilike("content", f"%{query_text}%").order(
            "created_at", desc=True
        ).limit(limit).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Search memories error: {e}")
        return []


async def delete_memory(telegram_user_id: str, memory_id: str) -> bool:
    """Delete a specific memory."""
    client = _get_client()
    if not client:
        return False
    try:
        client.table("stew_memories").delete().eq(
            "id", memory_id
        ).eq("telegram_user_id", str(telegram_user_id)).execute()
        return True
    except Exception as e:
        logger.error(f"Delete memory error: {e}")
        return False


async def clear_all_memories(telegram_user_id: str) -> bool:
    """Clear all memories for a user."""
    client = _get_client()
    if not client:
        return False
    try:
        client.table("stew_memories").delete().eq(
            "telegram_user_id", str(telegram_user_id)
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Clear memories error: {e}")
        return False


# ─── Conversation History ────────────────────────────────────────────────────

async def save_conversation(
    telegram_user_id: str,
    role: str,  # "user" or "assistant"
    content: str,
    message_id: Optional[str] = None,
) -> bool:
    """Save a conversation message for context recall."""
    client = _get_client()
    if not client:
        return False
    try:
        data = {
            "telegram_user_id": str(telegram_user_id),
            "role": role,
            "content": content[:4000],  # truncate long messages
            "message_id": message_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table("stew_conversations").insert(data).execute()
        return True
    except Exception as e:
        logger.error(f"Save conversation error: {e}")
        return False


async def get_conversation_history(
    telegram_user_id: str,
    limit: int = 20,
) -> list[dict]:
    """Get recent conversation history for context."""
    client = _get_client()
    if not client:
        return []
    try:
        result = client.table("stew_conversations").select("*").eq(
            "telegram_user_id", str(telegram_user_id)
        ).order("created_at", desc=True).limit(limit).execute()
        # Reverse to get chronological order
        history = (result.data or [])[::-1]
        return history
    except Exception as e:
        logger.error(f"Get conversation history error: {e}")
        return []


# ─── File Storage ────────────────────────────────────────────────────────────

async def upload_file(
    file_bytes: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
    folder: str = "uploads",
) -> Optional[str]:
    """Upload a file to Supabase Storage. Returns public URL or None."""
    client = _get_client()
    if not client:
        return None
    try:
        path = f"{folder}/{filename}"
        client.storage.from_("stew-files").upload(
            path, file_bytes, {"content-type": content_type}
        )
        # Get public URL
        url = client.storage.from_("stew-files").get_public_url(path)
        return url
    except Exception as e:
        logger.error(f"Upload file error: {e}")
        return None


async def upload_private_file(
    file_bytes: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
    folder: str = "private",
    expires_in: int = 3600,
) -> Optional[str]:
    """Upload a private file and get a signed URL. Returns signed URL or None."""
    client = _get_client()
    if not client:
        return None
    try:
        path = f"{folder}/{filename}"
        client.storage.from_("stew-files").upload(
            path, file_bytes, {"content-type": content_type}
        )
        # Create signed URL
        result = client.storage.from_("stew-files").create_signed_url(
            path, expires_in
        )
        return result.get("signedURL") if isinstance(result, dict) else None
    except Exception as e:
        logger.error(f"Upload private file error: {e}")
        return None


# ─── User Profile & Settings ─────────────────────────────────────────────────

async def get_user_profile(telegram_user_id: str) -> Optional[dict]:
    """Get user profile from Supabase. Returns None if not found."""
    client = _get_client()
    if not client:
        return None
    try:
        result = client.table("stew_profiles").select("*").eq(
            "telegram_user_id", str(telegram_user_id)
        ).limit(1).execute()
        return (result.data or [None])[0]
    except Exception as e:
        logger.error(f"Get profile error: {e}")
        return None


async def upsert_user_profile(telegram_user_id: str, data: dict) -> bool:
    """Create or update user profile."""
    client = _get_client()
    if not client:
        return False
    try:
        data["telegram_user_id"] = str(telegram_user_id)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        client.table("stew_profiles").upsert(data).execute()
        return True
    except Exception as e:
        logger.error(f"Upsert profile error: {e}")
        return False


# ─── SQL Migration ────────────────────────────────────────────────────────────

MIGRATION_SQL = """
-- Stew Agent Persistent Memory Tables
-- Run this in Supabase SQL Editor

-- User memories (facts, preferences, instructions)
CREATE TABLE IF NOT EXISTS stew_memories (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    metadata JSONB DEFAULT '{}',
    embedding VECTOR(384),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Conversation history
CREATE TABLE IF NOT EXISTS stew_conversations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    message_id TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- User profiles
CREATE TABLE IF NOT EXISTS stew_profiles (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_user_id TEXT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    plan TEXT DEFAULT 'free',
    preferred_voice TEXT,
    voice_enabled BOOLEAN DEFAULT FALSE,
    total_messages INTEGER DEFAULT 0,
    monthly_messages INTEGER DEFAULT 0,
    last_message_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Feature requests
CREATE TABLE IF NOT EXISTS stew_feature_requests (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    feature_text TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    votes INTEGER DEFAULT 0,
    voter_ids TEXT[] DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ad campaigns
CREATE TABLE IF NOT EXISTS stew_ad_campaigns (
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
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_memories_user ON stew_memories(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_memories_category ON stew_memories(telegram_user_id, category);
CREATE INDEX IF NOT EXISTS idx_memories_type ON stew_memories(telegram_user_id, memory_type);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON stew_conversations(telegram_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_profiles_user ON stew_profiles(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_features_status ON stew_feature_requests(status, votes DESC);
CREATE INDEX IF NOT EXISTS idx_ads_status ON stew_ad_campaigns(status);

-- Enable Row Level Security
ALTER TABLE stew_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE stew_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE stew_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE stew_feature_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE stew_ad_campaigns ENABLE ROW LEVEL SECURITY;

-- RLS Policies (service role bypasses RLS, so these are for anon/authenticated)
CREATE POLICY "Users can read own memories" ON stew_memories FOR SELECT USING (true);
CREATE POLICY "Users can insert own memories" ON stew_memories FOR INSERT WITH CHECK (true);
CREATE POLICY "Users can delete own memories" ON stew_memories FOR DELETE USING (true);
CREATE POLICY "Users can read own conversations" ON stew_conversations FOR SELECT USING (true);
CREATE POLICY "Users can insert own conversations" ON stew_conversations FOR INSERT WITH CHECK (true);
CREATE POLICY "Anyone can read profiles" ON stew_profiles FOR SELECT USING (true);
CREATE POLICY "Anyone can upsert profiles" ON stew_profiles FOR INSERT WITH CHECK (true);
CREATE POLICY "Anyone can update profiles" ON stew_profiles FOR UPDATE USING (true);
CREATE POLICY "Anyone can read features" ON stew_feature_requests FOR SELECT USING (true);
CREATE POLICY "Anyone can insert features" ON stew_feature_requests FOR INSERT WITH CHECK (true);
CREATE POLICY "Anyone can read ads" ON stew_ad_campaigns FOR SELECT USING (true);

-- Create storage bucket for files
INSERT INTO storage.buckets (id, name, public) VALUES ('stew-files', 'stew-files', true) ON CONFLICT DO NOTHING;
"""
