CREATE TABLE IF NOT EXISTS text_comments (
    id TEXT PRIMARY KEY,
    text_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    content TEXT NOT NULL,
    parent_id TEXT DEFAULT '',
    likes INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (text_id) REFERENCES texts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS text_comment_likes (
    comment_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY (comment_id, user_id)
);
