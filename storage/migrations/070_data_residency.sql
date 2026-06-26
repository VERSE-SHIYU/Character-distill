-- ============================================================
-- 070 — Data residency groundwork (SQLite)
--
-- 1. home_region column on users
-- 2. user_secrets table (physically separate sensitive fields)
-- 3. Migrate password_hash/api_key/base_url/model into user_secrets
--
-- Column removal is handled by the Python runner via table
-- recreate (SQLite DROP COLUMN requires >= 3.35.0).
-- ============================================================

-- ---- 1. home_region ----
ALTER TABLE users ADD COLUMN home_region TEXT NOT NULL DEFAULT 'cn-shenzhen';

-- ---- 2. user_secrets table ----
CREATE TABLE IF NOT EXISTS user_secrets (
    user_id       TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,
    api_key       TEXT DEFAULT '',
    base_url      TEXT DEFAULT '',
    model         TEXT DEFAULT ''
);

-- ---- 3. Data migration (idempotent: skip if already populated) ----
INSERT INTO user_secrets (user_id, password_hash, api_key, base_url, model)
SELECT u.id, u.password_hash, u.api_key, u.base_url, u.model
FROM users u
WHERE NOT EXISTS (SELECT 1 FROM user_secrets s WHERE s.user_id = u.id);
