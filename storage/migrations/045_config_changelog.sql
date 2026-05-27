-- 045_config_changelog.sql: Track admin config changes
CREATE TABLE IF NOT EXISTS config_changelog (
    id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL,
    admin_username TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
