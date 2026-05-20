ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0;
ALTER TABLE users ADD COLUMN is_disabled BOOLEAN DEFAULT 0;

CREATE TABLE IF NOT EXISTS invite_codes (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    created_by TEXT NOT NULL,
    used_by TEXT DEFAULT NULL,
    used_at TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
