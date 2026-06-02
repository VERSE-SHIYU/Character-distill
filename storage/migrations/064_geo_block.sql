CREATE TABLE IF NOT EXISTS geo_block_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    ip         TEXT NOT NULL DEFAULT '',
    base_url   TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
