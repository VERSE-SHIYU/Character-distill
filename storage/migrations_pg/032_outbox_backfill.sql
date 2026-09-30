-- ============================================================
-- 032 — 存量补发：本机用掉的邀请码入队（「已使用」）
--
-- 发件箱（跨境同步）是这次才落地的，上线前就用掉的码从没入过队 —— 对端永远不知道它们已被使用。
--
-- **幂等**：`ON CONFLICT (op_type, target_id) DO NOTHING`。账本为空时 `_run_migrations`
-- 会把全部迁移重放一遍（存量库首次上账本），正文必须可重跑。
--
-- 存量用户资料不在这里：见 `033_backfill_profile_sync.sql`（资料的 payload 是版本戳，
-- 发送时才读最新资料）。
-- ============================================================

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
