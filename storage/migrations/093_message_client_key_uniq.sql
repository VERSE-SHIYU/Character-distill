-- 093 — 幂等键唯一索引（SQLite 侧）：同一条消息无论重放几次都只有一行
--
-- **为什么单独一个文件、为什么编号在 092 之后**：`_apply_migration` 的判据是「脚本里每个
-- ADD COLUMN 的列都已存在 → 整份脚本跳过」。索引若与加列同文件，第一次应用之后那份脚本恒被
-- SKIP，索引就只剩「首次应用那一轮」一次机会：那一轮里若索引语句失败（列加上了、索引没建成），
-- 之后每次启动都跳过、永远补不回来，**也不报错** —— 静默少一个兜底。本文件不含 ADD COLUMN，
-- 每轮都真跑。先例：088 → 090（SQLite）、021 → 023（PG）。
--
-- partial index：只约束非空 key。NULL 表示「这次写入没带幂等键」，彼此之间没有唯一性可言
-- —— 老调用点连着写两条相同内容是对的，也正是现在的行为（见 tests/test_client_key_idempotency.py）。
--
-- 索引是**兜底**不是主路径：正常写入先在应用层按 key 查重（save_message）。这里的价值是让
-- 「两个 key 相同却都到了 INSERT」不可能悄悄发生 —— 会响亮地报唯一约束错，而不是留两行。
CREATE UNIQUE INDEX IF NOT EXISTS messages_session_client_key_uniq
    ON messages(session_id, client_key)
 WHERE client_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS group_messages_group_client_key_uniq
    ON group_messages(group_id, client_key)
 WHERE client_key IS NOT NULL;
