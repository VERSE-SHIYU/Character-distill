-- 104 — group_session_records（PG 039 的 SQLite 孪生；列集锁要求两侧相等）
-- 语义见 PG 039 与 docs/specs/group-session-record.md。
CREATE TABLE IF NOT EXISTS group_session_records (
    group_id       TEXT NOT NULL,
    card_id        TEXT NOT NULL,
    session_record TEXT NOT NULL,
    PRIMARY KEY (group_id, card_id)
);
