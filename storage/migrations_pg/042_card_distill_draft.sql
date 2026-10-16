-- ============================================================
-- 042 — cards.distill_draft：蒸馏时模型产出的「草稿」（E7）
--
-- 一个 JSON 字符串 `{"character": <蒸馏时的角色名>, "draft": <转卡前的模型产出>}`。
-- 用途：改了转卡规则后，用存下的草稿重跑 `card_from_draft` 重新出卡，不必重蒸整本书
-- （第 2 段的功能，本迁移只为它留位）。
--
-- NULL = 没有草稿：旧卡（加这列之前蒸的）、同步通道（`get_or_distill` 的草稿转完即丢）、
-- 复制 / 发布出来的卡。只由 `save_card_draft` 写（`TextManager.save_distilled_card` 一处调用）。
--
-- 内部列、不出服务端：读卡方法（`get_card_owned` / `get_card_unscoped`）都写明列名，
-- 这一列不会随卡片行返回前端；导出只读 `card_json`。存的是**审核前**的模型产出，
-- 第 2 段重转时必须重新过注入审核。
--
-- 幂等：`IF NOT EXISTS`。SQLite 孪生：107（列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE cards ADD COLUMN IF NOT EXISTS distill_draft TEXT;
