-- 111 — texts.chapters_json / chapters_version（PG 046 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 046 与 core/chapters.py。SQLite 没有 ADD COLUMN IF NOT EXISTS，重复执行靠
-- 执行器读 PRAGMA 前置（`storage/sqlite_store.py::_apply_migration`），故此处是裸 ADD COLUMN。
ALTER TABLE texts ADD COLUMN chapters_json TEXT;
ALTER TABLE texts ADD COLUMN chapters_version INTEGER DEFAULT 0;
