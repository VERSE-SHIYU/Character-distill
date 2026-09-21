-- ============================================================
-- 020 — cards.published_from：把「草稿 → 作者自己的发布副本」独立成一列
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
-- 幂等：本文件每轮 init 都会重跑（PG 侧没有「已应用」账本）。列用 ADD COLUMN IF NOT
-- EXISTS；约束没有 IF NOT EXISTS 形态，用 DO 块吞掉重复创建（否则第二轮 init 直接失败）。
-- ============================================================

ALTER TABLE cards ADD COLUMN IF NOT EXISTS published_from TEXT DEFAULT NULL;

-- 复合外键的目标：`(id, user_id)` 上必须有唯一约束。`id` 已是主键，故本约束恒成立
-- （加约束不会因存量数据失败），它的全部作用是给下面那条 FK 一个可指向的目标。
DO $$
BEGIN
    ALTER TABLE cards ADD CONSTRAINT cards_id_user_id_key UNIQUE (id, user_id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

-- 为什么是 DEFERRABLE INITIALLY DEFERRED（**实测选的，不是随手加的**）：
-- 非推迟的 FK 检查排在用户 AFTER 触发器**之前**（把触发器改名排到 `RI_ConstraintTrigger_*`
-- 前面也一样，实测无效），于是下面那条触发器还没来得及把副本清空，检查就先报
-- ForeignKeyViolation。推迟到提交时检查，触发器与检查的次序才成立。
-- 代价：违例在 COMMIT 报错而非语句当场 —— 本列只由本迁移与回填写，可接受。
-- 也**不能**退回 `BEFORE DELETE` 触发器：PG 禁止触发器改「同一命令还要删的行」，
-- 多行删除（按 text_id / 按 user_id / texts 级联）实测直接报
-- TriggeredDataChangeViolation。
DO $$
BEGIN
    ALTER TABLE cards ADD CONSTRAINT cards_published_from_fkey
        FOREIGN KEY (published_from, user_id) REFERENCES cards(id, user_id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

-- 硬删有 4 条路径（purge_card / 按 text_id / 按 user_id / SQLite 侧启动去重）。
-- 只清其中一条，其余三条删除一张卡时会撞上面那条外键。
-- 把汇合点放进库里：任一路径删除一张卡，指向它的副本的 `published_from` 就地置空，
-- 副本本身存活 —— 一处定义覆盖全部路径，不靠在 4 个调用点各记得清一次。
-- 必须是 AFTER（理由见上）；`RETURN NULL` 是 AFTER 触发器的返回值惯例（结果被忽略）。
CREATE OR REPLACE FUNCTION cards_clear_published_from() RETURNS trigger AS $$
BEGIN
    UPDATE cards SET published_from = NULL WHERE published_from = OLD.id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cards_clear_published_from ON cards;
CREATE TRIGGER trg_cards_clear_published_from
    AFTER DELETE ON cards
    FOR EACH ROW EXECUTE FUNCTION cards_clear_published_from();
