-- ============================================================
-- 024 — users.role：身份从 is_admin 布尔列收敛成三值单列（加列 + 回填）
--
-- 与 SQLite 091 同一主题、同一语义。PG 侧没有「已应用」账本，每轮 init 全量重放每个
-- 迁移文件（见执行器与 021 的说明），所以「只跑一次」只能靠**幂等谓词现算**：本文件的
-- 谓词是 `users.role` 列是否存在。列不存在 ⇒ 首次应用，加列 + 回填一次做完；列已存在
-- ⇒ 整块跳过（含回填）。
--
-- **回填必须一次性**：否则管理员在后台被降级之后，下一次启动会被回填重新提回 admin。
--
-- 回填读 `is_admin`，而删它的是 025（编号更大的独立文件，排在后面）—— 顺序由文件名
-- 排序保证，不靠 DO 块内部时序。
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'role'
    ) THEN
        ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'
            CHECK (role IN ('admin','user','guest'));
        UPDATE users SET role = 'admin' WHERE is_admin = 1;
    END IF;
END $$;
