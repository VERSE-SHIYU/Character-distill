-- 110 — texts.characters_trace（PG 045 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 045 与 core/distiller.py::identify_roster。SQLite 没有 ADD COLUMN IF NOT
-- EXISTS，重复执行靠执行器读 PRAGMA 前置（`storage/sqlite_store.py::_apply_migration`），
-- 故此处是裸 ADD COLUMN。
ALTER TABLE texts ADD COLUMN characters_trace TEXT;
