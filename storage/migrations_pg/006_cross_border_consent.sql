-- ============================================================
-- 006 — Cross-border consent table + cross_border_synced (PG)
-- ============================================================

CREATE TABLE IF NOT EXISTS cross_border_consent (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       TEXT NOT NULL,
    target_region TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'direct_message',
    granted_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, target_region, scope)
);

ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS cross_border_synced SMALLINT DEFAULT 1;
