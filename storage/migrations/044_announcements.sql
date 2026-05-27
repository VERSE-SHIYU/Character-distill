-- 044_announcements.sql: Create announcements table
CREATE TABLE IF NOT EXISTS announcements (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
