CREATE TABLE IF NOT EXISTS user_posts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    visibility TEXT DEFAULT 'public',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
