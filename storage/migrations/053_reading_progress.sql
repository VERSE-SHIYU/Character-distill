CREATE TABLE IF NOT EXISTS reading_progress (
    user_id TEXT NOT NULL,
    text_id TEXT NOT NULL,
    progress REAL DEFAULT 0,
    scroll_position INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, text_id)
);
