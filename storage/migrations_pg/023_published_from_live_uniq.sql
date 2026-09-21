-- ============================================================
-- 023 — 「一草稿至多一张存活副本」的部分唯一索引（PG 侧）
--
-- 裁定：下架 = 撤回发布 —— 副本行仍在，只是不再在架，重发复用同一行。故唯一性不看
-- visibility，只看「未删」：`WHERE deleted_at IS NULL`。
--
-- **为什么编号在 021 之后、排在 022 之后**：022 是回填并收敛（尚未落地）。存量库里同一
-- 草稿可能有多张存活副本（旧语义每发布一次新建一行）。实测在含两张同草稿存活副本的库上：
--   · 索引先建、再回填 → 建索引成功（`published_from` 全为 NULL，唯一索引不互撞），
--     回填报 `UNIQUE constraint failed: cards.published_from`
--   · 回填先、再建索引 → 回填成功，建索引报同一个错
-- 两种顺序都让 init 失败，只是炸在不同语句；**光换顺序不解决，收敛才是解**。故顺序排成
-- 加列(021) → 回填并收敛(022) → 建唯一索引(本文件)。实测「回填 → 收敛（多余副本软删）
-- → 建索引」三步全通过，存活副本 1 张。
--
-- 幂等：PG 侧每轮 init 全量重放（无「已应用」账本，见交接台账），`IF NOT EXISTS` 使本句
-- 可直接重跑。SQLite 侧同序见 `storage/migrations/090_published_from_live_uniq.sql`；
-- 那一侧还多一条成因（`_apply_migration` 的整份跳过规则），与 PG 无关。
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS cards_published_from_live_uniq
    ON cards(published_from)
 WHERE deleted_at IS NULL;
