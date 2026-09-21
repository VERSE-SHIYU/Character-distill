-- ============================================================
-- 021 — cards.published_from：把「草稿 → 作者自己的发布副本」独立成一列
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
-- 存量的 NULL user_id 卡规模由只读查询另行统计。
--
-- 删除草稿时副本怎么处理，由 FK 自己的动作承担：`ON DELETE SET NULL (published_from)`
-- —— 只置空副本的 `published_from`，`user_id` 原样保留，副本存活。**列清单是关键**
-- （PG 15+ 语法）：不带清单的 `SET NULL` 会把整条外键的每一列都置空，`user_id` 一丢，
-- 副本就不再属于原主，且 MATCH SIMPLE 下本约束对它彻底失效 —— 一个「解绑」动作反而
-- 把副本放出了约束之外。用原生动作而不写触发器，是为了不依赖「用户触发器与外键检查
-- 谁先跑」这种时序细节：4 条硬删路径（purge / 按 text_id / 按 user_id / texts 级联）
-- 只在库里汇合一次，不靠每个调用点记得清。
--
-- 幂等：本文件每轮 init 都会重跑（PG 侧没有「已应用」账本，见交接台账）。列用 ADD COLUMN
-- IF NOT EXISTS；约束没有 IF NOT EXISTS 形态，用 DO 块吞掉重复创建（否则第二轮 init
-- 直接失败）。也因此改本文件的约束定义，只对**新建库**生效，存量库要手工 DROP 再加 ——
-- 本文件尚未合入 main、未上生产，故直接改在原地，不写兼容旧定义的探测/清理。
-- ============================================================

ALTER TABLE cards ADD COLUMN IF NOT EXISTS published_from TEXT DEFAULT NULL;

-- 复合外键的目标：`(id, user_id)` 上必须有唯一约束。`id` 已是主键，故本约束恒成立
-- （加约束不会因存量数据失败），它的全部作用是给下面那条 FK 一个可指向的目标。
DO $$
BEGIN
    ALTER TABLE cards ADD CONSTRAINT cards_id_user_id_key UNIQUE (id, user_id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE cards ADD CONSTRAINT cards_published_from_fkey
        FOREIGN KEY (published_from, user_id) REFERENCES cards(id, user_id)
        ON DELETE SET NULL (published_from);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

-- 「一草稿至多一张存活副本」由库强制（裁定：下架 = 撤回发布，副本行仍在，只是不再在架，
-- 重发复用同一行）。故唯一性不看 visibility，只看「未删」。
--
-- **回填须先收敛多副本**：存量库里若同一 published_from 有多行未删副本，本索引建不上，
-- 迁移会当场报错（这是想要的 —— 静默跳过就等于没约束）。收敛顺序见步骤 3 的回填迁移。
CREATE UNIQUE INDEX IF NOT EXISTS cards_published_from_live_uniq
    ON cards(published_from)
 WHERE deleted_at IS NULL;
