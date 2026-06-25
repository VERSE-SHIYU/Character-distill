CREATE TABLE IF NOT EXISTS dm_reactions (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(message_id, user_id, emoji)
);
