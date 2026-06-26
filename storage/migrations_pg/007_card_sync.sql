-- ============================================================
-- 007 — Card cross-border sync status (PG)
-- ============================================================

ALTER TABLE cards ADD COLUMN IF NOT EXISTS cross_border_synced SMALLINT DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_cards_cross_border_unsynced ON cards(cross_border_synced) WHERE cross_border_synced = 0;
