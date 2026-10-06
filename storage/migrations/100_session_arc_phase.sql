-- 100 — sessions.arc_phase（PG 035 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 035 与 docs/specs/arc-phase-select.md。
ALTER TABLE sessions ADD COLUMN arc_phase INTEGER;
