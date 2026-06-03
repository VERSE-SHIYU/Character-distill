CREATE TABLE IF NOT EXISTS user_consent (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    terms_version   TEXT NOT NULL,
    privacy_version TEXT NOT NULL,
    ip              TEXT NOT NULL DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now'))
);
