-- ============================================================
-- 021 — cards.published_from：把「草稿 → 作者自己的发布副本」独立成一列（加列 + 约束 + 回填并收敛）
--
-- 背景：`forked_from` 单列同时承载两种关系 ——「草稿 → 作者自己的发布副本」与
-- 「公开卡 → 任意用户的 fork」。两者都满足 `forked_from = D.id AND visibility = 'public'
-- AND deleted_at IS NULL`，区分它们**唯一**的判据是 `copy.user_id = draft.user_id`，
-- 而这条判据此前要每个调用点自己记得写（漏一处即静默跨属主写）。
-- 拆列后本列只表示前者，并由复合外键在库里强制「同一作者」。
--
-- 为什么用复合外键而不是代码判断：`forked_from` 的教训是「关系定义散在 10 处、漏一处
-- 就出事」。`(published_from, user_id) REFERENCES cards(id, user_id)` 让写入即验，
-- 判据从「每个调用点记得比」变成「库拒绝」，不依赖任何一处源码写对。
--
-- **MATCH SIMPLE 的边界（已知缺口，不是笔误）**：SQL 标准下复合外键任一列为 NULL
-- 即不检查，所以 `user_id IS NULL` 的卡，其 `published_from` 不受本约束约束。
-- 存量 NULL user_id 卡的规模见只读审计 ⑤。
--
-- 删除草稿时副本怎么处理，由 FK 自己的动作承担：`ON DELETE SET NULL (published_from)`
-- —— 只置空副本的 `published_from`，`user_id` 原样保留，副本存活。**列清单是关键**
-- （PG 15+ 语法）：不带清单的 `SET NULL` 会把整条外键的每一列都置空，`user_id` 一丢，
-- 副本就不再属于原主，且 MATCH SIMPLE 下本约束对它彻底失效 —— 一个「解绑」动作反而
-- 把副本放出了约束之外。用原生动作而不写触发器，是为了不依赖「用户触发器与外键检查
-- 谁先跑」这种时序细节：4 条硬删路径（purge / 按 text_id / 按 user_id / texts 级联）
-- 只在库里汇合一次，不靠每个调用点记得清。
--
-- ============================================================
-- 为什么整份包在一个「列不存在」的 DO 块里
--
-- PG 侧没有「已应用」账本，每轮 init 全量重放每个迁移文件（见执行器与交接台账），
-- 所以「只跑一次」不能靠账本，只能靠**幂等谓词现算**。本文件的谓词是
-- `cards.published_from` **列是否存在**：列不存在 ⇒ 首次应用，加列 + 约束 + 回填 + 收敛
-- 一次做完（整文件本就是一个隐式事务，DO 块内失败即全部回滚）；列已存在 ⇒ 整块跳过。
--
-- **回填必须一次性，这是本案的根本命题**：fork 路由允许 fork 自己的公开卡
-- （`web/routers/market.py` 的 `POST /{card_id}/fork`）—— 用户 fork 自己那张公开的发布副本，
-- 就写出一条**同属主 `forked_from`** 的合法行（`fork_card` 只写 forked_from，从不碰
-- published_from）。回填若每次启动重跑，会把这种合法自我 fork 改判成发布副本：语义被写坏，
-- 且同一张副本被 fork 两次就出现两行同 `published_from` → 撞 023 的唯一索引 → 启动失败。
-- 约束也放在这个分支里，是因为它们的创建同样只该在首次发生（放在外面就要靠
-- exception-swallow 吞重复，而那份「吞掉」正是本块要消灭的形态）。
--
-- **谓词**：与代码里唯一那份关系谓词同义 —— `_PUBLISHED_COPY_OF`
-- （storage/postgres_store.py 顶部，SQLite 侧 storage/sqlite_store.py 同形）。「关系」= 属主
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
-- 实测读数（走只读脚本 scripts/audit_published_from_prebackfill.sql，2026-09-21）：
-- ④ 疑似自 fork 0 行、⑤ user_id IS NULL 0 行、⑥ 同草稿多张存活副本 0 行（两地基线非空：
-- SG 34 张卡 / 4 条 forked_from；SZ 17 / 3）。故存量两库上本段是空操作，不特判；
-- 对任何其它形态的库（旧 pg_dump 备份恢复）本段按其谓词自行成立。
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cards' AND column_name = 'published_from'
    ) THEN
        ALTER TABLE cards ADD COLUMN published_from TEXT DEFAULT NULL;

        -- 复合外键的目标：`(id, user_id)` 上必须有唯一约束。`id` 已是主键，故本约束恒成立
        -- （加约束不会因存量数据失败），它的全部作用是给下面那条 FK 一个可指向的目标。
        ALTER TABLE cards ADD CONSTRAINT cards_id_user_id_key UNIQUE (id, user_id);

        ALTER TABLE cards ADD CONSTRAINT cards_published_from_fkey
            FOREIGN KEY (published_from, user_id) REFERENCES cards(id, user_id)
            ON DELETE SET NULL (published_from);

        -- 回填并收敛（与 SQLite 侧 088 尾部同一份语句，逐字同形）。
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
    END IF;
END $$;

-- 「一草稿至多一张存活副本」的部分唯一索引**不在这里**、在 023：编号必须排在回填并收敛
-- （本文件）之后。存量库里同一草稿可能有多张存活副本，回填不加收敛就撞唯一索引 —— 实测在
-- 含两张同草稿存活副本的库上，索引先建/回填先建两种顺序都让 init 失败，只是炸在不同语句。
-- 成因与实测逐条写在 023 顶部。
