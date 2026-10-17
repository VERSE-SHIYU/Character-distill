-- ============================================================
-- 043 — texts.retro_json / retro_version：这本书的「回忆区」（日记、书信、番外、整章回忆）
--
-- 一个 JSON 字符串 `{"status", "reason", "ranges": [[首章, 末章, 类别], ...]}`，由
-- `core/retro_cache.py::resolve_retro_regions` 一处写入；语义见 `core/retro_regions.py`。
-- 回忆区是**作品的属性**（同 characters_json）：同书换角色蒸、用草稿重新转卡都复用这一份。
--
-- retro_version 由调用方给（`core.retro_regions.RETRO_VERSION`），存储层只负责比对；
-- DEFAULT 0 = 从未标过，与任何版本都不匹配，等于无缓存。
--
-- 幂等：`IF NOT EXISTS`；不写 UPDATE / DELETE（每次启动重跑）。SQLite 孪生：108（列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE texts ADD COLUMN IF NOT EXISTS retro_json TEXT;
ALTER TABLE texts ADD COLUMN IF NOT EXISTS retro_version INTEGER DEFAULT 0;
