-- ============================================================
-- 037 — sessions.session_record：一对一存档的「本场记录」（④ 段 1）
--
-- 一个 JSON 字符串（格式只由 `core/session_record.SessionRecord.to_persist` 定）。NULL = 还没有。
-- 只由 `save_session_record` 写；会话读取方法都写明列名，这一列不会出现在返回前端的会话行里
-- （本场记录不显示给用户，docs/specs/session-record.md）。
--
-- 幂等：`IF NOT EXISTS`。SQLite 孪生：102（列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_record TEXT;
