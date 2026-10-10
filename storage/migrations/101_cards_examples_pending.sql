-- 101 — cards.examples_pending_for（PG 036 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 036 与 docs/specs/examples-pending.md。
ALTER TABLE cards ADD COLUMN examples_pending_for TEXT;
