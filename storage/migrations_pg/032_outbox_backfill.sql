-- ============================================================
-- 032 — 存量补发：本机每个用户的公开资料、本机用掉的邀请码入队
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

-- 存量「已使用」邀请码：本机用掉的码入队，对端看不到的也一样。
--
-- **`used_by = 'peer'` 的排除掉**：那是从对端同步来、写在这边的「已使用」，再发回去就是
-- 回传（对端早记着了，收到会当成新变更）。
--
-- payload 只有 code（政策 3.2(4)：只同步「已被使用」这个状态，不含使用者身份）。
INSERT INTO cross_border_outbox (op_type, target_id, payload)
SELECT 'invite_used', c.code,
       json_build_object('code', c.code)::text
FROM invite_codes c
WHERE c.used_by IS NOT NULL AND c.used_by <> 'peer'
ON CONFLICT (op_type, target_id) DO NOTHING;
