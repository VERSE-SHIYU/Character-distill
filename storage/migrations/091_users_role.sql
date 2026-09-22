-- 091 — users.role：身份从 is_admin 布尔列收敛成三值单列（加列 + 回填）
--
-- 本文件**只加列与回填，不删 is_admin**。删列由 sqlite_store.py 的退役列块每轮复现地做，
-- 理由见那里的结构化名单：013_admin.sql 每轮启动都会把 is_admin 加回来（与 018 加回
-- api_key/base_url/model 同形），所以一次性 DROP 删不掉 —— 第二次启动它就复活成默认值 0。
--
-- **必须登记在 `_MIGRATIONS_BEFORE_USER_REBUILD` 段**：回填要读到 is_admin，而退役列块
-- 排在 BEFORE 段之后、AFTER 段之前（实测）。把本文件放进 AFTER 段，回填就是
-- `no such column: is_admin`。
--
-- 「只跑一次」的谓词就是下面这句 ADD COLUMN：`_apply_migration` 在「脚本里每个 ADD COLUMN
-- 的列都已存在」时**整份跳过**（含尾部数据段）。所以第二轮回填不会重跑 —— 否则管理员在
-- 后台被降级之后，下一次启动会被回填重新提回 admin。这条时序由
-- tests/test_users_role_migration.py 钉着。
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'
    CHECK (role IN ('admin','user','guest'));

UPDATE users SET role = 'admin' WHERE is_admin = 1;
