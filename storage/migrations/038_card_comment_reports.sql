-- Comment reporting system: track user reports on card comments
CREATE TABLE IF NOT EXISTS card_comment_reports (
    id TEXT PRIMARY KEY,
    comment_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolver_id TEXT,
    FOREIGN KEY (comment_id) REFERENCES card_comments(id) ON DELETE CASCADE,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);
