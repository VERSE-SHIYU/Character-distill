-- 095 — users.timezone：用户最后已知时区（空串 = 未知）
--
-- 与 PG `migrations_pg/028_users_timezone.sql` 同一主题、同一语义。本文件是
-- `storage/migrations/README.md` 里那条**唯一例外**的实例：PG 新增列时，这里补一份
-- 同语义的孪生迁移，否则两侧真库的列集锁会红，而那条锁不许开豁免。
--
-- **幂等靠 `_apply_migration` 读现状**：脚本里每个 ADD COLUMN 的列都已存在 ⇒ 整份跳过。
-- SQLite 没有 `ADD COLUMN IF NOT EXISTS`，故这里写裸 ADD COLUMN。
--
-- 登记在 `sqlite_store.py` 的 `_MIGRATIONS_AFTER_USER_REBUILD` 段末尾：它与退役列块
-- （那段边界）互不相干，排段尾是为保住「段内编号单调」。

ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT '';
