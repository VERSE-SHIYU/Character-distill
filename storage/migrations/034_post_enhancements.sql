ALTER TABLE user_posts ADD COLUMN images TEXT DEFAULT '';
ALTER TABLE user_posts ADD COLUMN card_id TEXT DEFAULT '';
ALTER TABLE user_posts ADD COLUMN likes INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS post_likes (
    user_id TEXT NOT NULL,
    post_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, post_id)
);

CREATE TABLE IF NOT EXISTS post_comments (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
