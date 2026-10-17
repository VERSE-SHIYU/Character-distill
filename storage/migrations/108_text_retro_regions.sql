-- 108 — texts.retro_json / retro_version（PG 043 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 043 与 core/retro_regions.py。SQLite 没有 ADD COLUMN IF NOT EXISTS，重复执行靠
-- 执行器读 PRAGMA 前置（`storage/sqlite_store.py::_apply_migration`），故此处是裸 ADD COLUMN。
ALTER TABLE texts ADD COLUMN retro_json TEXT;
ALTER TABLE texts ADD COLUMN retro_version INTEGER DEFAULT 0;
