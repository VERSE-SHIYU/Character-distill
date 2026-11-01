-- ============================================================
-- 045 — texts.characters_trace：这份名单是**怎么来的**（识别留证）
--
-- 一个 JSON 字符串，由 `core/character_roster.py::resolve_characters` 与名单**同一次
-- UPDATE** 写入：识别口径、模型、分片大小、逐片原始条目、全书判定的 5 份原始输出与
-- 票数结果。语义见 `core/distiller.py::identify_roster` 的 trace 结构。
--
-- 为什么与 `characters_json` 同一行、同一次写入：名单与留证必须来自同一次识别，
-- 分开写会出现「新名单配旧留证」，任何一次错并都追不下去（D24 ①b）。
-- trace 为 NULL = 这份名单没有留证（旧数据 / 非识别路径），不是空识别。
--
-- 幂等：`IF NOT EXISTS`；不写 UPDATE / DELETE（每次启动重跑）。SQLite 孪生：110
-- （列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE texts ADD COLUMN IF NOT EXISTS characters_trace TEXT;
