-- ============================================================
-- 009 — Delete propagation outbox + DM retracted column
--
-- 1. DM retracted column (for retracting DMs on remote node)
-- 2. Delete propagation outbox (cards, DMs, user purge)
--
-- Re-entrant: all IF NOT EXISTS / IF EXISTS guarded.
-- Column design aligned with SQLite 074 + 075.
-- ============================================================

ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS retracted INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS cross_border_delete_outbox (
    id          BIGSERIAL PRIMARY KEY,
    op_type     TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    payload     TEXT DEFAULT '',
    synced      INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(op_type, target_id)
);
