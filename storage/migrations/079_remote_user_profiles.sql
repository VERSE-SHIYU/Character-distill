CREATE TABLE IF NOT EXISTS remote_user_profiles (
    id          TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    home_region TEXT NOT NULL DEFAULT '',
    avatar_data TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
