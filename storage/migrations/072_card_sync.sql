-- ============================================================
-- 072 — Card cross-border sync status
-- ============================================================

ALTER TABLE cards ADD COLUMN cross_border_synced INTEGER DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_cards_cross_border_unsynced ON cards(cross_border_synced);
