CREATE TABLE IF NOT EXISTS cross_border_delete_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type     TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    payload     TEXT DEFAULT '',
    synced      INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(op_type, target_id)
);
