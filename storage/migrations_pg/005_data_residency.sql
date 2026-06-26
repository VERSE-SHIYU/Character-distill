-- ============================================================
-- 005 — Data residency groundwork
--
-- 1. home_region column on users (cn-shenzhen / sg-singapore)
-- 2. user_secrets table — physically separate sensitive fields
-- 3. Migrate password_hash/api_key/base_url/model out of users
-- 4. Drop those 4 columns from users
--
-- Re-entrant: all IF NOT EXISTS / IF EXISTS guarded.
-- ============================================================

-- ---- 1. home_region ----
ALTER TABLE users ADD COLUMN IF NOT EXISTS home_region TEXT NOT NULL DEFAULT 'cn-shenzhen';
CREATE INDEX IF NOT EXISTS idx_users_home_region ON users(home_region);

-- ---- 2. user_secrets table ----
CREATE TABLE IF NOT EXISTS user_secrets (
    user_id       TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,
    api_key       TEXT DEFAULT '',
    base_url      TEXT DEFAULT '',
    model         TEXT DEFAULT ''
);

-- ---- 3. Data migration (conditional — only if columns still exist) ----
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'password_hash'
    ) THEN
        INSERT INTO user_secrets (user_id, password_hash, api_key, base_url, model)
        SELECT id, password_hash, api_key, base_url, model FROM users
        ON CONFLICT (user_id) DO NOTHING;
    END IF;
END $$;

-- ---- 4. Drop columns from users (idempotent) ----
ALTER TABLE users DROP COLUMN IF EXISTS password_hash;
ALTER TABLE users DROP COLUMN IF EXISTS api_key;
ALTER TABLE users DROP COLUMN IF EXISTS base_url;
ALTER TABLE users DROP COLUMN IF EXISTS model;
