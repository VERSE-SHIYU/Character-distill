-- 092 — 消息幂等键列：messages.client_key / group_messages.client_key（SQLite 侧）
--
-- 为什么要这一列：补写队列（core/message_outbox.py）会在「写失败后重放」与「用户点重试」
-- 两条路径上把同一条消息写两次。幂等键让第二次写命中已有行、不再插第二行 —— 队列只知道
-- 一个 key 和一个 write_fn，不需要知道表结构。
--
-- NULL = 这次写入没带幂等键（老调用点、老数据）。唯一性只在非空 key 上成立，索引见
-- storage/migrations/093_message_client_key_uniq.sql（**必须**是另一个文件，理由写在那）。
--
-- SQLite 没有 ADD COLUMN IF NOT EXISTS，重复执行靠执行器读 PRAGMA 前置
-- （storage/sqlite_store.py::_apply_migration），故此处是裸 ADD COLUMN。
ALTER TABLE messages ADD COLUMN client_key TEXT DEFAULT NULL;
ALTER TABLE group_messages ADD COLUMN client_key TEXT DEFAULT NULL;
