ALTER TABLE users ADD COLUMN username_lower TEXT;
UPDATE users SET username_lower = LOWER(username) WHERE username_lower IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (username_lower);
