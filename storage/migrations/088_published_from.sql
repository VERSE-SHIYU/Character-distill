-- 088 — cards.published_from：把「草稿 → 作者自己的发布副本」独立成一列（加列 + 回填 + 收敛）
--
-- 背景与 PG 侧 021 是同一件事实（SQLite 侧的详细理由见 storage/postgres_store.py 与
-- sqlite_store.py 顶部的 `_PUBLISHED_COPY_OF` 定义块）：`forked_from` 单列同时承载
-- 「作者自己的发布副本」与「任意用户的 fork」两种关系，区分它们唯一的判据是
-- `copy.user_id = draft.user_id`。拆列后本列只表示前者，并由表级复合外键 + UNIQUE
-- 强制「同一作者」。
--
-- **表级 UNIQUE 与复合外键不在这里**：SQLite 加得了列、加得了索引，加不了这两件（要重建
-- 表）。它们走 `_ensure_initialized` 里的 `_rebuild_cards_published_from` —— 它按 PRAGMA
-- 现算列清单来重建，所以列必须先在这里存在。
--
-- 唯一索引**不在这里**、在 090：本文件含 ADD COLUMN，而 `_apply_migration` 的判据是
-- 「脚本里每个 ADD COLUMN 的列都已存在 → 整份跳过」—— 列一旦存在（本文件跑过之后就是），
-- 整份脚本连同尾部任何语句一起跳过，索引就永远建不上。成因与实测见 090 顶部。
--
-- 本列也是 SQLite 启动去重那条 DELETE 的判据之一（见 sqlite_store.py 的两条去重
-- DELETE）：发布副本的 `forked_from` 变成 '' 之后，不加 `published_from IS NULL`
-- 它会和作者的草稿同 text_id 同 name，被去重 DELETE 当成重复草稿删掉。
ALTER TABLE cards ADD COLUMN published_from TEXT DEFAULT NULL;

-- ============================================================
-- 回填并收敛 —— **只在本文件刚加上列的那一轮跑**
--
-- 这段是数据语句，它「只跑一次」不是靠账本，而是靠上面那句 ADD COLUMN：
-- `_apply_migration` 的判据是「本文件每个 ADD COLUMN 的列都已存在 → 整份跳过」，
-- 列存在即整份（含本段）跳过。执行器那份 docstring 本来就写着这条形态
-- （「这类脚本尾部常跟一段数据回填 UPDATE/INSERT，语义上只属于首次应用」）。
--
-- **为什么必须一次性（本案的根本命题）**：fork 路由允许 fork 自己的公开卡
-- （`web/routers/market.py` 的 `POST /{card_id}/fork`）—— 用户 fork 自己那张公开的发布
-- 副本，就写出一条**同属主 `forked_from`** 的合法行（`fork_card` 只写 forked_from，
-- 从不碰 published_from）。回填若每次启动重跑，会把这种合法自我 fork 改判成发布副本：
-- 语义被写坏，且同一张副本被 fork 两次就出现两行同 `published_from` → 撞唯一索引 → 启动失败。
--
-- **谓词**：与代码里唯一那份关系谓词同义 —— `_PUBLISHED_COPY_OF`
-- （storage/sqlite_store.py 顶部，PG 侧 storage/postgres_store.py 同形）。「关系」= 属主
-- 相同（本仓由复合外键 `(published_from, user_id) → cards(id, user_id)` 在库里强制，
-- 谓词里写不出函数，故这里写成 `c.user_id = d.user_id`）。**不看 visibility**：
-- 下架 = 撤回发布，副本行仍在架外存活、仍是发布副本。
--
-- **谓词带 `c.deleted_at IS NULL`、也带 `c.published_from IS NULL`**，两条都在关系谓词
-- 自身的形状里（`_PUBLISHED_COPY_OF` 就是 `published_from = D.id AND deleted_at IS NULL`）：
-- 前者与「收敛只数存活副本」一致，故软删的副本保持 forked_from 原样、成为普通 fork；
-- 后者让本段自身幂等（即便被重复执行，已回填的行不再入选）。
--
-- **收敛**（同一草稿多张存活副本时留哪一张）：按 (likes desc, 该副本被 fork 数 desc,
-- created_at desc, id desc) 排名取第一张写 `published_from` 并清 `forked_from`。
-- **落选的不动** —— `forked_from` 原样保留，它们成为普通 fork（正是「本行不再是发布副本」
-- 的正确表达）。不许 DELETE、不许改 visibility。
--
-- 实测读数（走只读脚本 scripts/audit_published_from_prebackfill.sql，2026-09-21 SG 22:56 /
-- SZ 22:57）：④ 疑似自 fork 0 行、⑤ user_id IS NULL 0 行、⑥ 同草稿多张存活副本 0 行
-- （两地基线非空：SG 34 张卡 / 4 条 forked_from；SZ 17 / 3）。故存量两库上本段是空操作，
-- 不特判；对任何其它形态的库（本地 SQLite、旧 pg_dump 备份恢复）本段按其谓词自行成立。
-- ============================================================

WITH fork_count AS (
    -- 每张卡被 fork 的存活次数（收敛的第二个排序键）。
    SELECT f.forked_from AS card_id, COUNT(*) AS n
      FROM cards f
     WHERE f.forked_from <> '' AND f.deleted_at IS NULL
     GROUP BY f.forked_from
),
winner AS (
    SELECT c.id,
           ROW_NUMBER() OVER (
               PARTITION BY c.forked_from
               ORDER BY COALESCE(c.likes, 0) DESC,
                        COALESCE(fc.n, 0) DESC,
                        c.created_at DESC,
                        c.id DESC
           ) AS rn
      FROM cards c
      JOIN cards d ON d.id = c.forked_from
      LEFT JOIN fork_count fc ON fc.card_id = c.id
     WHERE c.forked_from <> ''
       AND c.user_id IS NOT NULL
       AND c.user_id = d.user_id
       AND c.deleted_at IS NULL
       AND c.published_from IS NULL
)
UPDATE cards
   SET published_from = forked_from,
       forked_from = ''
 WHERE id IN (SELECT id FROM winner WHERE rn = 1);
