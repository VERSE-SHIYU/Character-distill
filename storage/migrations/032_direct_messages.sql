CREATE TABLE IF NOT EXISTS direct_messages (
    id TEXT PRIMARY KEY,
    sender_id TEXT NOT NULL,
    receiver_id TEXT NOT NULL,
    content TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dm_receiver ON direct_messages(receiver_id, is_read);
CREATE INDEX IF NOT EXISTS idx_dm_conversation ON direct_messages(sender_id, receiver_id);
