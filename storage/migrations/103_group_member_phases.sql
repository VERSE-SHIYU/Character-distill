-- 103 — group_sessions.member_phases（PG 038 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 038 与 docs/specs/group-arc-phase.md。
ALTER TABLE group_sessions ADD COLUMN member_phases TEXT NOT NULL DEFAULT '{}';
