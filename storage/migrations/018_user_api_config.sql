ALTER TABLE users ADD COLUMN api_key TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN base_url TEXT DEFAULT 'https://api.deepseek.com';
ALTER TABLE users ADD COLUMN model TEXT DEFAULT 'deepseek-v4-pro';
