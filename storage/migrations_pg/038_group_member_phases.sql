-- ============================================================
-- 038 — group_sessions.member_phases：建群时给每个 AI 人物选的时期（JSON：卡 id → 阶段号）
--
-- 只存选了且有效的（`valid_phase` 非 None）；没出现的卡 = 最后阶段。只存阶段号，不存投影后的卡
-- —— 怎么投影只由 `core/arc_view.project_card` 一处算（与一对一 `sessions.arc_phase` 同口径）。
-- 只在建群时写一次；建群后不能改（要换时期就新建群）。
--
-- 幂等：`IF NOT EXISTS`。SQLite 孪生：103（列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE group_sessions ADD COLUMN IF NOT EXISTS member_phases TEXT NOT NULL DEFAULT '{}';
