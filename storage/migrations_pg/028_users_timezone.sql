-- ============================================================
-- 028 — users.timezone：用户最后已知时区（空串 = 未知）
--
-- 与 SQLite 095 同一主题、同一语义。写入方只有一个 —— 请求入口（`web/server.py` 的
-- `AuthMiddleware`）按 GitHub 的顺序确定本次请求时区（`Time-Zone` 请求头 → 本列 → 不设），
-- 头部有效且与已存值不同时异步回写本列；读取方是 `get_user_by_id`。业务代码不直接碰它，
-- 取时间一律走 `UserClock`（它读请求入口设的 ContextVar，读不到才回退 DEFAULT_TZ）。
--
-- 空串而不是 NULL：`NOT NULL DEFAULT ''` 让「未知」只有一个写法，读取侧不必分诊
-- NULL / '' 两态。
--
-- **幂等靠 `IF NOT EXISTS`，不用 DO 块现算信息模式**：执行器按 `schema_migrations` 账本只跑
-- 未记账的文件，本文件正常只会跑一次；`IF NOT EXISTS` 是**没有账本时**的兜底（账本落地前的
-- 老库、以及任何把 `schema_migrations` 删掉的重放），让这句重跑时仍是 no-op（与 010 / 027 同形）。
--
-- 本文件在 SQLite 侧**必须有孪生文件**（`storage/migrations/095_users_timezone.sql`）：
-- 两侧真库的列集锁要求相等且不许开豁免，理由见 `storage/migrations/README.md`。
-- ============================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT '';
