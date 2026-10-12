-- ============================================================
-- 039 — group_session_records：群聊里每个 AI 人物的「本场记录」（⑤，docs/specs/group-session-record.md）
--
-- 与一对一 `sessions.session_record`（037）同一格式：一个 JSON 字符串，格式只由
-- `core/session_record.SessionRecord.to_persist` 定。一行 = （群, 人物）。
-- 单独成表而不挂在 `group_affinity` 上：那张表「有这一行 = 评估过好感」（成员标签据此显示
-- 「尚未评估」），本场记录先建行会伪造好感 50。
-- 只由 `save_group_session_record` 写；读取只有 `load_group_session_record_unscoped`（恢复群引擎），
-- 不进任何返回前端的结果。硬删群时一起删（`hard_delete_group_session`）。
--
-- 幂等：`IF NOT EXISTS`。SQLite 孪生：104（列集锁要求两侧相等）。
-- ============================================================

CREATE TABLE IF NOT EXISTS group_session_records (
    group_id       TEXT NOT NULL,
    card_id        TEXT NOT NULL,
    session_record TEXT NOT NULL,
    PRIMARY KEY (group_id, card_id)
);
