CREATE TABLE IF NOT EXISTS message_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(message_id, user_id, emoji)
);

ALTER TABLE group_messages ADD COLUMN reply_to_id INTEGER DEFAULT NULL;
ALTER TABLE group_messages ADD COLUMN reply_to_preview TEXT DEFAULT '';
