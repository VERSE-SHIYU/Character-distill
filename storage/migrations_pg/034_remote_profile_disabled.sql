-- ============================================================
-- 034 — remote_user_profiles.is_disabled：对端用户的账号状态（0 正常 / 1 已禁用）
--
-- 由资料同步（outbox `user_profile`）随 3.2(1) 公开字段一起带过来。读取方：全局搜索排除
-- 已禁用的对端用户；作者主页对已禁用用户只给「已封禁」视图。与本地 `users.is_disabled`
-- 同类型同取值，两侧判据一致。
--
-- 回填：本库**已禁用**的用户各入队一条 `user_profile`，让存量禁用状态发到对端（发送时现读
-- 最新资料，见 `cross_border_sync.forward_user_profile_to_peer`；只发本地区用户，过滤在发送方）。
-- 已有待发行保持原样（DO NOTHING）：它本身就会发最新资料。
--
-- 幂等：`IF NOT EXISTS` + `ON CONFLICT`，与 028 / 033 同形。SQLite 孪生：099。
-- ============================================================

ALTER TABLE remote_user_profiles ADD COLUMN IF NOT EXISTS is_disabled SMALLINT NOT NULL DEFAULT 0;

INSERT INTO cross_border_outbox (op_type, target_id, payload)
SELECT 'user_profile', id, gen_random_uuid()::text
FROM users
WHERE is_disabled = 1
ON CONFLICT (op_type, target_id) DO NOTHING;
