-- 088 — cards.published_from：把「草稿 → 作者自己的发布副本」独立成一列
--
-- 背景与 PG 侧 021 是同一件事实（SQLite 侧的详细理由见 storage/postgres_store.py 与
-- sqlite_store.py 顶部的 `_PUBLISHED_COPY_OF` 定义块）：`forked_from` 单列同时承载
-- 「作者自己的发布副本」与「任意用户的 fork」两种关系，区分它们唯一的判据是
-- `copy.user_id = draft.user_id`。拆列后本列只表示前者，并由表级复合外键 + UNIQUE
-- 强制「同一作者」。
--
-- **本文件只加列与索引**：SQLite 加得了列、也加得了索引，加不了表级 UNIQUE 与复合外键
-- （那两件要重建表）。复合约束走 `_ensure_initialized` 里的 `_rebuild_cards_published_from`
-- —— 它按 PRAGMA 现算列清单来重建，所以列必须先在这里存在。
--
-- 本列也是 SQLite 启动去重那条 DELETE 的判据之一（见 sqlite_store.py 的两条去重
-- DELETE）：发布副本的 `forked_from` 变成 '' 之后，不加 `published_from IS NULL`
-- 它会和作者的草稿同 text_id 同 name，被去重 DELETE 当成重复草稿删掉。
ALTER TABLE cards ADD COLUMN published_from TEXT DEFAULT NULL;

-- 「一草稿至多一张存活副本」由库强制（裁定：下架 = 撤回发布，副本行仍在，只是不再在架，
-- 重发复用同一行）。故唯一性不看 visibility，只看「未删」。
--
-- **回填须先收敛多副本**：存量库里若同一 published_from 有多行未删副本，本索引建不上，
-- 迁移会当场报错（这是想要的 —— 静默跳过就等于没约束）。收敛顺序见步骤 3 的回填迁移。
CREATE UNIQUE INDEX IF NOT EXISTS cards_published_from_live_uniq
    ON cards(published_from)
 WHERE deleted_at IS NULL;
