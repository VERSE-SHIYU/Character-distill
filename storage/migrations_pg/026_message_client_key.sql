-- ============================================================
-- 026 — 消息幂等键：messages.client_key / group_messages.client_key（PG 侧）
--
-- 理由与 SQLite 侧的 092 / 093 是同一件事实：补写队列会在「写失败后重放」与「用户点重试」
-- 两条路径上把同一条消息写两次，幂等键让第二次写命中已有行。
--
-- NULL = 没带幂等键（老调用点、老数据）；唯一性只在非空 key 上成立（partial index）。
--
-- **为什么这里加列与建索引可以同文件，SQLite 侧却要拆两份**：PG 侧一份文件只跑一次
-- （`schema_migrations` 账本），加列与建索引必然在同一趟里都执行到，不存在「加列让整份
-- 被跳过、索引再没机会」那种形态；`IF NOT EXISTS` 只是没账本时的兜底。SQLite 侧被
-- `_apply_migration` 的「整份跳过」规则波及，拆文件的理由写在 093 里，与本文件无关。
-- ============================================================

ALTER TABLE messages ADD COLUMN IF NOT EXISTS client_key TEXT DEFAULT NULL;
ALTER TABLE group_messages ADD COLUMN IF NOT EXISTS client_key TEXT DEFAULT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS messages_session_client_key_uniq
    ON messages(session_id, client_key)
 WHERE client_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS group_messages_group_client_key_uniq
    ON group_messages(group_id, client_key)
 WHERE client_key IS NOT NULL;
