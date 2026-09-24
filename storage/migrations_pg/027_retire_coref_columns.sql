-- ============================================================
-- 027 — 退役 texts.content_resolved / coref_resolved：共指消解整条链路已删
--
-- 与 SQLite 094 同一主题、同一语义。两列由 PG `001_init.sql` 的 CREATE TABLE 直接
-- 声明（SQLite 侧是 056 加的），只服务已删除的 `Distiller.coref_resolve` /
-- `DISTILL_USE_COREF` 开关，字段再无写入方。
--
-- **幂等靠 `IF EXISTS`，不用 DO 块现算信息模式**：PG 侧每轮 init 全量重放每个迁移
-- 文件（无「已应用」账本），这条语句第二次起就是 no-op，谓词「列存不存在」由 PG
-- 自己现算（与 025 同形）。
--
-- 001_init.sql 保持不动（历史迁移文件，改了会让「全新库」与「已建库」变成两个
-- schema —— 缺陷 26 的教训）；「建了再删」的净效果由本文件负责。
-- ============================================================

ALTER TABLE texts DROP COLUMN IF EXISTS content_resolved;
ALTER TABLE texts DROP COLUMN IF EXISTS coref_resolved;
