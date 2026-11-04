-- ============================================================
-- 046 — texts.chapters_json / chapters_version：这本书的章节表
--
-- 一个 JSON 数组 `[[标题行原文起点, 该行原文], ...]`，由
-- `core/chapters_cache.py::resolve_chapters` 一处写入；语义见 `core/chapters.py`。
-- 与回忆区同为**作品的属性**：同书换角色蒸、用草稿重新转卡都复用这一份。
--
-- chapters_version 由调用方给（`core.chapters.CHAPTERS_VERSION`），存储层只负责比对；
-- DEFAULT 0 = 从未判过，与任何版本都不匹配，等于无缓存。
--
-- 幂等：`IF NOT EXISTS`；不写 UPDATE / DELETE（每次启动重跑）。SQLite 孪生：111（列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE texts ADD COLUMN IF NOT EXISTS chapters_json TEXT;
ALTER TABLE texts ADD COLUMN IF NOT EXISTS chapters_version INTEGER DEFAULT 0;
