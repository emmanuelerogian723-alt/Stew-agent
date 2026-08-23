-- Mood DNA table — tracks user emotional patterns
CREATE TABLE IF NOT EXISTS mood_entries (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mood VARCHAR(30) NOT NULL,
    mood_score INTEGER DEFAULT 50,
    energy_score INTEGER DEFAULT 50,
    message_snippet VARCHAR(200),
    day_of_week INTEGER DEFAULT 0,
    hour_of_day INTEGER DEFAULT 12,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mood_entries_user_id ON mood_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_mood_entries_created_at ON mood_entries(created_at);
