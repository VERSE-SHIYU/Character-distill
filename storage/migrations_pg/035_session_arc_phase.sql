-- ============================================================
-- 035 — sessions.arc_phase：本次会话选定的角色弧线阶段（NULL = 不截断，用最后阶段）
--
-- 用户开聊前在身份弹窗里选「这次聊的是哪个时期的他」，角色只按那个阶段及之前的状态说话
-- （docs/specs/arc-phase-select.md）。这里只存阶段号，不存投影后的卡 —— 怎么投影只由
-- `core/arc_view.project_card` 一处算。
--
-- 只在 `/start_session` 写一次（`set_session_arc_phase`）；`save_session` 不碰这一列（C13），
-- 故聊天中改身份不会把阶段冲掉。
--
-- 幂等：`IF NOT EXISTS`。SQLite 孪生：100（列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS arc_phase INTEGER;
