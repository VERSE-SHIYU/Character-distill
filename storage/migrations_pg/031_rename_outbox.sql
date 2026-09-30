-- ============================================================
-- 031 — cross_border_delete_outbox → cross_border_outbox
--
-- 这张表早已不只装删除操作（用户资料、邀请码的新增与「已使用」都走它），旧名在误导人。
-- 只改名，不动列、不动数据。
--
-- **必须可重跑**：`postgres_store._run_migrations` 明确支持「账本为空 = 存量库首次上
-- 账本」时把全部迁移重放一遍（判据见 tests/test_message_evidence.py::
-- test_second_init_keeps_the_column_and_the_rows）。裸改名的第二次会撞 `no such table`，
-- 所以按现状判断，用的是本目录既有的 information_schema 手法（005 / 024 同款）。
--
-- 但**只看源表在不在就改名**还不够：重放路径上，更早的 009 同样是
-- `CREATE TABLE IF NOT EXISTS`，会把旧名重新建出来（空壳），而真身早已是改名后的那张
-- —— 这时再 RENAME 就撞 `relation "cross_border_outbox" already exists`（重放用例实测）。
-- 所以分两支：目标**不在**才改名；目标已在，就把 009 刚建的空壳删掉（真实数据在第一轮
-- 的 RENAME 里就搬走了，同一批次内 009 与 031 之间没有任何东西写这张表，故它必为空）。
-- 空壳非空即停下来报错，不许静默丢数据。
--
-- SQLite 孪生是 `098_rename_outbox.sql`：那边没有 DO 块，同一套判据由执行器
-- `sqlite_store._apply_migration` 的 RENAME 支承担。
-- ============================================================

DO $$
DECLARE
    stale_rows bigint;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'cross_border_delete_outbox'
    ) THEN
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'cross_border_outbox'
        ) THEN
            SELECT count(*) INTO stale_rows FROM cross_border_delete_outbox;
            IF stale_rows > 0 THEN
                RAISE EXCEPTION
                    'cross_border_delete_outbox 与 cross_border_outbox 同时存在，且旧表有 % 行'
                    '—— 改名早已生效，这张旧表不该有数据（回放重建的只会是空壳）。'
                    '不自动删，人工确认后再处理。', stale_rows;
            END IF;
            DROP TABLE cross_border_delete_outbox;
        ELSE
            ALTER TABLE cross_border_delete_outbox RENAME TO cross_border_outbox;
        END IF;
    END IF;
END $$;
