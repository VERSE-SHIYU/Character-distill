CREATE TABLE IF NOT EXISTS remote_cards (
    id                  TEXT PRIMARY KEY,
    origin_region       TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    name                TEXT NOT NULL,
    card_json           TEXT NOT NULL,
    avatar_data         TEXT DEFAULT '',
    market_description   TEXT DEFAULT '',
    market_tags         TEXT DEFAULT '',
    origin_created_at   TEXT DEFAULT '',
    synced_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
