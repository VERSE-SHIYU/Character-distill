-- ============================================================
-- 回填前只读审计：cards.forked_from → published_from 的回填策略
--
-- **只读**（无 INSERT/UPDATE/DELETE/DDL），在**生产库**上跑，结果决定后续那条回填
-- 迁移怎么写。必须在回填**之前**跑：回填会改 `forked_from` 与 `published_from`，
-- 跑完这些查询就再也回答不了「原样是什么」。
--
-- 本文件只含追加的 ④⑤⑥；①②③ 已在调研阶段交付。④ 自带 ① 的行集（CTE 重述），
-- 故可单独执行，不必依赖 ①②③ 先跑。
-- ============================================================


-- ④ ① 的行中，d（被指向的卡）自身在 card_versions 里有记录的行 —— 疑似「自 fork」。
--
-- 为什么值得单独数：`card_versions` 的行由 `publish_card` / `update_published_card`
-- 写入，两者写的都是**发布副本自己的 id**。所以 d 在 card_versions 里有行 ⇒ d 本身
-- 是某个人的发布副本，而 c 却把 d 当作「自己的草稿」指过去。这与「草稿 → 作者自己的
-- 发布副本」不是同一种关系，回填若照 `user_id` 相等一律写成 `published_from`，
-- 这批行会被记成发布副本（语义漂移）。规模决定回填要不要为它们特判。
WITH published_copy AS (
    -- ① 的行集：现存「公开、未删、且与所指卡同属主」的 forked_from 关系。
    SELECT c.id AS copy_id, c.user_id AS copy_user, c.forked_from AS draft_id
      FROM cards c
      JOIN cards d ON d.id = c.forked_from
     WHERE c.visibility = 'public'
       AND c.deleted_at IS NULL
       AND c.user_id = d.user_id
)
SELECT p.copy_id,
       p.copy_user,
       p.draft_id,
       d.user_id                       AS draft_user,
       d.visibility                    AS draft_visibility,
       (d.deleted_at IS NOT NULL)      AS draft_is_deleted,
       (SELECT COUNT(*) FROM card_versions v WHERE v.card_id = p.draft_id) AS draft_version_rows
  FROM published_copy p
  JOIN cards d ON d.id = p.draft_id
 WHERE EXISTS (SELECT 1 FROM card_versions v WHERE v.card_id = p.draft_id)
 ORDER BY p.copy_id;


-- ⑤ `cards.user_id IS NULL` 的行数（含明细）。
--
-- 为什么值得单独数：复合外键 `(published_from, user_id) → cards(id, user_id)` 在
-- **MATCH SIMPLE** 下的边界 —— 任一列为 NULL 即整条不检查。所以 `user_id IS NULL`
-- 的卡，其 `published_from` 不受约束约束，是本次拆列留下的一处已知缺口。
-- 规模决定这缺口是「当场补掉」还是「记账另议」。
SELECT COUNT(*) AS cards_with_null_user_id FROM cards WHERE user_id IS NULL;

SELECT id, text_id, name, visibility, forked_from, deleted_at
  FROM cards
 WHERE user_id IS NULL
 ORDER BY id;


-- ⑥ 同一草稿挂多张存活的同属主副本（不看 visibility）。
--
-- 为什么值得单独数：旧语义「每发布一次新建一行」会留下同一草稿的多张存活副本。
-- 唯一索引 `cards_published_from_live_uniq ON cards(published_from) WHERE deleted_at IS NULL`
-- 要求一个 `published_from` 至多一张存活行，故回填遇到这批行必撞索引（实测：两个顺序
-- 都让 init 失败，炸在不同语句）。规模决定收敛那步（写在 088 尾部 / 021 的 DO 块里）要不要特判留哪一张。
-- 注意：这里**不看 visibility** —— 下架 = 撤回发布，副本行仍存活，仍占唯一性。
SELECT d.id AS draft_id, COUNT(*) AS live_copies,
       array_agg(c.id || ':' || c.visibility || ':' || c.created_at ORDER BY c.created_at) AS copies
  FROM cards c JOIN cards d ON d.id = c.forked_from
 WHERE c.user_id = d.user_id AND c.deleted_at IS NULL
 GROUP BY d.id HAVING COUNT(*) > 1;
