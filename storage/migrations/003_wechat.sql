-- 003_wechat.sql
-- WeChat Work user → session mapping

CREATE TABLE IF NOT EXISTS wechat_users (
    openid TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    card_id TEXT REFERENCES cards(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
