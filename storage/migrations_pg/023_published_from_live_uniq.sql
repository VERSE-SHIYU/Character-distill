-- ============================================================
-- 023 — 「一草稿至多一张存活副本」的部分唯一索引（PG 侧）
--
-- 裁定：下架 = 撤回发布 —— 副本行仍在，只是不再在架，重发复用同一行。故唯一性不看
-- visibility，只看「未删」：`WHERE deleted_at IS NULL`。
--
-- **为什么编号在 021 之后**：021 里既有加列也有回填并收敛（同一个 DO 块：列不存在才整块
-- 跑）。存量库里同一草稿可能有多张存活副本（旧语义每发布一次新建一行）。实测在含两张同
-- 草稿存活副本的库上：
--   · 索引先建、再回填 → 建索引成功（`published_from` 全为 NULL，唯一索引不互撞），
--     回填报 `UNIQUE constraint failed: cards.published_from`
--   · 回填先、再建索引 → 回填成功，建索引报同一个错
-- 两种顺序都让 init 失败，只是炸在不同语句；**光换顺序不解决，收敛才是解**。故顺序排成
-- 加列 + 回填并收敛(021) → 建唯一索引(本文件)。收敛不走独立文件，是为了让「只跑一次」
-- 与加列共用同一个谓词（列已存在则整块跳过）—— 当时两侧都还没有迁移账本，这是不用账本
-- 也能成立的做法；账本（`schema_migrations`）是后来才加的，两侧的整块跳过/账本判据至今
-- 并存。实测「回填 → 收敛（只留一张拿 published_from，其余落回普通 fork）→ 建索引」
-- 三步全通过，存活副本 1 张。
--
-- 幂等：执行器按 `schema_migrations` 账本只跑未记账的文件，本句正常只会跑一次；
-- `IF NOT EXISTS` 是**没有账本时**的兜底（账本落地前的老库、以及任何把
-- `schema_migrations` 删掉的重放），使本句重跑时仍可直接执行。SQLite 侧同序见
-- `storage/migrations/090_published_from_live_uniq.sql`；那一侧还多一条成因
-- （`_apply_migration` 的整份跳过规则），与 PG 无关。
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS cards_published_from_live_uniq
    ON cards(published_from)
 WHERE deleted_at IS NULL;
