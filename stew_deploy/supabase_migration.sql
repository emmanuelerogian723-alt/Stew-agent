-- ────────────────────────────────────────────────────────────────────
-- STEW AGENT — Supabase Migration (Persistent Memory & Storage)
-- ────────────────────────────────────────────────────────────────────
-- Run this in your Supabase project's SQL Editor:
-- https://app.supabase.com → Your Project → SQL Editor → New Query
-- Paste everything below and click Run.
-- ────────────────────────────────────────────────────────────────────

-- 1. USER MEMORIES (facts, preferences, instructions, notes)
CREATE TABLE IF NOT EXISTS stew_memories (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'fact',  -- fact, preference, instruction, note
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    metadata JSONB DEFAULT '{}',
    embedding VECTOR(384),  -- for future semantic search via pgvector
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. CONVERSATION HISTORY (persistent chat context)
CREATE TABLE IF NOT EXISTS stew_conversations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- 'user' or 'assistant'
    content TEXT NOT NULL,
    message_id TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. USER PROFILES (settings, plan, voice preferences)
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

-- 4. FEATURE REQUESTS (community voting)
CREATE TABLE IF NOT EXISTS stew_feature_requests (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    feature_text TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    votes INTEGER DEFAULT 0,
    voter_ids TEXT[] DEFAULT '{}',
    status TEXT DEFAULT 'pending',  -- pending, in_progress, completed, rejected
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. AD CAMPAIGNS (monetization)
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

-- 6. INDEXES (fast lookups)
CREATE INDEX IF NOT EXISTS idx_memories_user ON stew_memories(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_memories_category ON stew_memories(telegram_user_id, category);
CREATE INDEX IF NOT EXISTS idx_memories_type ON stew_memories(telegram_user_id, memory_type);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON stew_conversations(telegram_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_profiles_user ON stew_profiles(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_features_status ON stew_feature_requests(status, votes DESC);
CREATE INDEX IF NOT EXISTS idx_ads_status ON stew_ad_campaigns(status);

-- 7. ROW LEVEL SECURITY
ALTER TABLE stew_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE stew_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE stew_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE stew_feature_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE stew_ad_campaigns ENABLE ROW LEVEL SECURITY;

-- RLS Policies (service role key bypasses RLS, so these allow anon access for bot use)
CREATE POLICY "read_memories" ON stew_memories FOR SELECT USING (true);
CREATE POLICY "insert_memories" ON stew_memories FOR INSERT WITH CHECK (true);
CREATE POLICY "update_memories" ON stew_memories FOR UPDATE USING (true);
CREATE POLICY "delete_memories" ON stew_memories FOR DELETE USING (true);

CREATE POLICY "read_conversations" ON stew_conversations FOR SELECT USING (true);
CREATE POLICY "insert_conversations" ON stew_conversations FOR INSERT WITH CHECK (true);
CREATE POLICY "delete_conversations" ON stew_conversations FOR DELETE USING (true);

CREATE POLICY "read_profiles" ON stew_profiles FOR SELECT USING (true);
CREATE POLICY "insert_profiles" ON stew_profiles FOR INSERT WITH CHECK (true);
CREATE POLICY "update_profiles" ON stew_profiles FOR UPDATE USING (true);

CREATE POLICY "read_features" ON stew_feature_requests FOR SELECT USING (true);
CREATE POLICY "insert_features" ON stew_feature_requests FOR INSERT WITH CHECK (true);
CREATE POLICY "update_features" ON stew_feature_requests FOR UPDATE USING (true);

CREATE POLICY "read_ads" ON stew_ad_campaigns FOR SELECT USING (true);
CREATE POLICY "insert_ads" ON stew_ad_campaigns FOR INSERT WITH CHECK (true);
CREATE POLICY "update_ads" ON stew_ad_campaigns FOR UPDATE USING (true);

-- 8. STORAGE BUCKET for file uploads (PDFs, videos, images)
INSERT INTO storage.buckets (id, name, public) 
VALUES ('stew-files', 'stew-files', true) 
ON CONFLICT DO NOTHING;

-- Storage policies (allow public read, authenticated write)
CREATE POLICY "Public read" ON storage.objects FOR SELECT USING (bucket_id = 'stew-files');
CREATE POLICY "Authenticated write" ON storage.objects FOR INSERT 
  WITH CHECK (bucket_id = 'stew-files');
CREATE POLICY "Authenticated update" ON storage.objects FOR UPDATE 
  USING (bucket_id = 'stew-files');

-- ────────────────────────────────────────────────────────────────────
-- DONE! Now add these environment variables to your Render service:
-- SUPABASE_URL = https://yourproject.supabase.co
-- SUPABASE_KEY = your_service_role_key (not the anon key)
-- ────────────────────────────────────────────────────────────────────
