CREATE TABLE IF NOT EXISTS featured_cards (
  id TEXT PRIMARY KEY,
  card_id TEXT NOT NULL,
  sort_order INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (card_id) REFERENCES cards(id)
);
