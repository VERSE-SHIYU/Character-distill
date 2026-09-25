-- 096 — 帖子评论、卡片评论的点赞表与计数列
--
-- 与 PG `migrations_pg/029_comment_likes.sql` 同一主题、同一语义。本文件是
-- `storage/migrations/README.md` 里那条**唯一例外**的实例：PG 加表/加列时，这里补一份
-- 同语义的孪生迁移，否则两侧真库的列集锁会红，而那条锁不许开豁免。
--
-- **幂等靠 `_apply_migration` 读现状**：SQLite 没有 `ADD COLUMN IF NOT EXISTS`，故这里
-- 写裸 ADD COLUMN。两个 ADD 的列都已存在 ⇒ 整份跳过，此时两张表一定在建它们的**首轮**
-- 就已建出（同一份脚本），所以跳过 CREATE TABLE 不会留下缺口。
--
-- 登记在 `sqlite_store.py` 的 `_MIGRATIONS_AFTER_USER_REBUILD` 段末尾（段内编号单调）。

CREATE TABLE IF NOT EXISTS post_comment_likes (
    comment_id TEXT NOT NULL REFERENCES post_comments(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (comment_id, user_id)
);

CREATE TABLE IF NOT EXISTS card_comment_likes (
    comment_id TEXT NOT NULL REFERENCES card_comments(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (comment_id, user_id)
);

ALTER TABLE post_comments ADD COLUMN likes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_comments ADD COLUMN likes INTEGER NOT NULL DEFAULT 0;
