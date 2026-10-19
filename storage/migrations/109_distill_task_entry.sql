-- 109 — distill_tasks.force / entry（PG 044 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 044 与 docs/specs/distill-entry-audit.md。SQLite 没有 ADD COLUMN IF NOT EXISTS，
-- 重复执行靠执行器读 PRAGMA 前置（`storage/sqlite_store.py::_apply_migration`），故此处是裸
-- ADD COLUMN。
ALTER TABLE distill_tasks ADD COLUMN force INTEGER NOT NULL DEFAULT 0;
ALTER TABLE distill_tasks ADD COLUMN entry TEXT NOT NULL DEFAULT '';
