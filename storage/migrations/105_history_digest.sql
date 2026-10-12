-- 105 — sessions.history_digest（PG 040 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 040 与 docs/specs/history-digest.md。
ALTER TABLE sessions ADD COLUMN history_digest TEXT;
