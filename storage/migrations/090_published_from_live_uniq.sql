-- 090 — 「一草稿至多一张存活副本」的部分唯一索引（SQLite 侧）
--
-- 裁定：下架 = 撤回发布 —— 副本行仍在，只是不再在架，重发复用同一行。故唯一性不看
-- visibility，只看「未删」：`WHERE deleted_at IS NULL`。
--
-- **为什么单独一个文件、为什么编号在 088 之后**：两条独立的理由，各自有实测。
--
-- ① 不放在 088 里（成因：整份迁移被跳过）。`_apply_migration` 的判据是「脚本里每个
--    ADD COLUMN 的列都已存在 → 整份跳过」。088 含 ADD COLUMN，列一旦存在（088 跑过之后
--    就是），整份脚本连同尾部语句一起跳过。实测：在同一形态的库上跑 init，
--    `_apply_migration` 对 088 的决策是 SKIP，而索引仍然存在 —— 它来自
--    `_ensure_initialized` 尾部另写的那句；把那句钝化掉，索引就没有了。即「两份定义」
--    正是这条跳过规则造成的。本文件不含 ADD COLUMN，实测连跑两次均 APPLY（不会被跳过），
--    故索引的定义只留这一处，init 尾部那句已删。
--    （另测：SQLite 的表重建**不会**丢索引 —— `_rebuild_cards_published_from` 按
--    sqlite_master 重放它；同一次重建会丢触发器，那才是 sqlite_store.py 里对应那句的成因。）
--
-- ② 编号必须在**回填并收敛之后**。存量库里同一草稿可能有多张存活副本（旧语义每发布一次
--    新建一行）。实测在含两张同草稿存活副本的库上：
--      · 索引先建、再回填 → 建索引成功（`published_from` 全为 NULL，唯一索引不互撞），
--        回填报 `UNIQUE constraint failed: cards.published_from`
--      · 回填先、再建索引 → 回填成功，建索引报同一个错
--    两种顺序都让 init 失败，只是炸在不同语句；**光换顺序不解决，收敛才是解**。
--    回填并收敛不走独立文件，就在 088 尾部（同一份 SKIP 谓词管住它，故只跑一次）；
--    顺序是：加列 + 回填并收敛(088) → 建唯一索引(本文件)。实测「回填 → 收敛
--    （只留一张拿 published_from，其余落回普通 fork）→ 建索引」三步全通过，存活副本 1 张。
CREATE UNIQUE INDEX IF NOT EXISTS cards_published_from_live_uniq
    ON cards(published_from)
 WHERE deleted_at IS NULL;
