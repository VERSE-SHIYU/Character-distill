-- 107 — cards.distill_draft（PG 042 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 042 与 docs/specs/distill-draft-persist.md。
ALTER TABLE cards ADD COLUMN distill_draft TEXT;
