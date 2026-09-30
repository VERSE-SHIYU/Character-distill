-- ============================================================
-- 032 — 存量补发：本机每个用户的公开资料入队
--
-- 发件箱（跨境同步）是这次才落地的，上线前就存在的用户从没入过队 —— 对端永远看不到他们。
-- 这条迁移把本机 `users` 里每个人入队一条 `user_profile`。
--
-- **只发本机自己的用户**：真源取 `users`，**不碰 `remote_user_profiles`** —— 那是从对端
-- 收来的资料，再入队就是回传（对端会把它当成新变更）。
--
-- **幂等**：`ON CONFLICT (op_type, target_id) DO NOTHING`。两个理由都要：
--   1. 账本为空时 `_run_migrations` 会把全部迁移重放一遍（存量库首次上账本）；
--   2. 队里可能已经有该用户更新的资料（注册 / 换头像时入的队），**旧值不许盖掉新值**
--      —— 所以是 DO NOTHING 而不是 DO UPDATE。
--
-- payload 与运行期同形（`postgres_store._profile_payload`）：只四个字段
-- id / username / home_region / avatar_data（隐私政策 3.2(1)：不含昵称、注册时间）。
-- ============================================================

INSERT INTO cross_border_outbox (op_type, target_id, payload)
SELECT 'user_profile', u.id,
       json_build_object(
           'id', u.id,
           'username', u.username,
           'home_region', COALESCE(u.home_region, ''),
           'avatar_data', COALESCE(u.avatar_data, '')
       )::text
FROM users u
ON CONFLICT (op_type, target_id) DO NOTHING;
