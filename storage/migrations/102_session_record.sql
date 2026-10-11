-- 102 — sessions.session_record（PG 037 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 037 与 docs/specs/session-record.md。
ALTER TABLE sessions ADD COLUMN session_record TEXT;
