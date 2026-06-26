-- ============================================================
-- 071 — Cross-border consent table + cross_border_synced
-- ============================================================

CREATE TABLE IF NOT EXISTS cross_border_consent (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    target_region TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'direct_message',
    granted_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, target_region, scope)
);

-- cross_border_synced: 1 = synced (default for same-region), 0 = pending/unsent
ALTER TABLE direct_messages ADD COLUMN cross_border_synced INTEGER DEFAULT 1;
