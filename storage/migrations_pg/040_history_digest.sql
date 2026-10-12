-- ============================================================
-- 040 — sessions.history_digest：一对一存档的「历史摘要块」（P13）
--
-- 一个 JSON 字符串（格式只由 `core/history_digest.Digests.to_persist` 定）。NULL = 还没有。
-- 只由 `save_history_digest` 写；会话读取方法都写明列名，这一列不会出现在返回前端的会话行里
-- （历史摘要块内部、不显示给用户，docs/specs/history-digest.md）。
--
-- 幂等：`IF NOT EXISTS`。SQLite 孪生：105（列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS history_digest TEXT;
