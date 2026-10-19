-- ============================================================
-- 044 — distill_tasks.force / entry：记录每次起蒸馏时「是否重新蒸馏、从哪个入口发起」。
--
-- 只写不读：用于排查「点 A 却蒸了 B」，直接查数据库；/task/{id} 与读任务方法都不带出
-- 这两列（内部审计列）。续跑复用旧行时，两列改成最近一次请求的值。
--
-- 幂等：IF NOT EXISTS。SQLite 孪生：109（列集锁要求两侧相等）。
-- ============================================================

ALTER TABLE distill_tasks ADD COLUMN IF NOT EXISTS force BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE distill_tasks ADD COLUMN IF NOT EXISTS entry TEXT NOT NULL DEFAULT '';
