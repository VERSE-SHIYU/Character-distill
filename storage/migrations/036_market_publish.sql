-- Market publish system: description, tags, publish message, version tracking

ALTER TABLE cards ADD COLUMN market_description TEXT DEFAULT '';
ALTER TABLE cards ADD COLUMN market_tags TEXT DEFAULT '';
ALTER TABLE cards ADD COLUMN publish_message TEXT DEFAULT '';

CREATE TABLE IF NOT EXISTS card_versions (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    version_num INTEGER NOT NULL DEFAULT 1,
    publish_message TEXT DEFAULT '',
    diff_json TEXT DEFAULT '{}',
    card_json_snapshot TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_card_versions_card ON card_versions(card_id);
