-- Card reporting system: track user reports on published cards
CREATE TABLE IF NOT EXISTS card_reports (
    id          TEXT PRIMARY KEY,
    card_id     TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ,
    resolver_id TEXT,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);
