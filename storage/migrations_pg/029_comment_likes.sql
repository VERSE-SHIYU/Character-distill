-- ============================================================
-- 029 — 帖子评论、卡片评论的点赞表与计数列
--
-- 与 SQLite `096_comment_likes.sql` 同一主题、同一语义。两侧真库的列集锁要求相等且
-- 不许开豁免，理由见 `storage/migrations/README.md`。
--
-- **两张表而不是一张多态表**：多态关联（kind 列 + comment_id 列）没法给 comment_id 加
-- 外键，一致性只能靠应用层自觉。本仓的 `text_comment_likes` 就是那个形态，它的现有后果
-- 是「点一条不存在的评论会插进一行孤儿记录」。每种评论各一张表，外键与级联由库保证。
--
-- **`user_id` 不加外键，`comment_id` 加**：`delete_user` 不删用户发过的评论，若 user_id
-- 带级联，删用户会删掉他的赞却不减计数 —— 计数与赞行就漂了。评论行没了则它的赞无所指，
-- 所以 comment_id 带 `ON DELETE CASCADE`。
--
-- **幂等靠 `IF NOT EXISTS`**：PG 执行器有 `schema_migrations` 账本，本文件正常只跑一次；
-- 这两个子句是账本落地前的老库与任何账本重放时的兜底（与 010 / 027 / 028 同形）。
-- ============================================================

CREATE TABLE IF NOT EXISTS post_comment_likes (
    comment_id TEXT NOT NULL REFERENCES post_comments(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (comment_id, user_id)
);

CREATE TABLE IF NOT EXISTS card_comment_likes (
    comment_id TEXT NOT NULL REFERENCES card_comments(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (comment_id, user_id)
);

ALTER TABLE post_comments ADD COLUMN IF NOT EXISTS likes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_comments ADD COLUMN IF NOT EXISTS likes INTEGER NOT NULL DEFAULT 0;
