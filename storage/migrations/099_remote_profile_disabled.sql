-- 099 — remote_user_profiles.is_disabled（PG 034 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 034。SQLite 不做存量回填：PG 是生产方向（ways-of-working 2026-09-23）。
ALTER TABLE remote_user_profiles ADD COLUMN is_disabled INTEGER NOT NULL DEFAULT 0;
