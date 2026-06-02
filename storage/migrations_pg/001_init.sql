-- ============================================================
-- PostgreSQL 初始化建表脚本（合并自 63 个 SQLite migration）
-- 放置路径: storage/migrations_pg/001_init.sql
-- 说明: PG 新库一次性建到位，不保留 SQLite 的逐步演进史。
--       所有 ALTER ADD COLUMN 已并入对应 CREATE TABLE。
-- 语法转换要点:
--   - SQLite INTEGER PRIMARY KEY AUTOINCREMENT  -> PG GENERATED ALWAYS AS IDENTITY
--   - SQLite datetime('now') / CURRENT_TIMESTAMP -> PG CURRENT_TIMESTAMP (TIMESTAMPTZ)
--   - SQLite BOOLEAN DEFAULT 0 / INTEGER 0/1 布尔位 -> PG SMALLINT DEFAULT 0
--     (保持 0/1 语义不变，避免改动 4400 行业务层对 0/1 的判断)
--   - REAL -> DOUBLE PRECISION
-- ============================================================

-- ---------- texts ----------
CREATE TABLE IF NOT EXISTS texts (
    id                  TEXT PRIMARY KEY,
    filename            TEXT NOT NULL,
    title               TEXT DEFAULT '',
    description         TEXT DEFAULT '',
    content             TEXT NOT NULL,
    char_count          INTEGER NOT NULL,
    text_type           TEXT DEFAULT 'story',
    original_char_count INTEGER DEFAULT NULL,
    characters_json     TEXT DEFAULT NULL,
    user_id             TEXT DEFAULT NULL,
    visibility          TEXT DEFAULT 'private',
    content_resolved    TEXT DEFAULT '',
    coref_resolved      SMALLINT DEFAULT 0,
    cover_data          TEXT DEFAULT '',
    deleted_at          TEXT DEFAULT '',
    created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------- cards ----------
CREATE TABLE IF NOT EXISTS cards (
    id                 TEXT PRIMARY KEY,
    text_id            TEXT,
    name               TEXT NOT NULL,
    card_json          TEXT NOT NULL,
    user_id            TEXT DEFAULT NULL,
    avatar_data        TEXT DEFAULT '',
    voice_ref_json     TEXT DEFAULT '',
    visibility         TEXT DEFAULT 'private',
    forked_from        TEXT DEFAULT '',
    likes              INTEGER DEFAULT 0,
    market_description TEXT DEFAULT '',
    market_tags        TEXT DEFAULT '',
    publish_message    TEXT DEFAULT '',
    updated_at         TEXT DEFAULT '',
    deleted_at         TEXT,
    created_at         TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (text_id) REFERENCES texts(id) ON DELETE CASCADE
);

-- ---------- sessions ----------
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    card_id         TEXT NOT NULL,
    user_role       TEXT DEFAULT '',
    avatar_data     TEXT DEFAULT '',
    user_id         TEXT DEFAULT NULL,
    voice_ref_json  TEXT DEFAULT '',
    deleted_at      TEXT DEFAULT NULL,
    affinity        INTEGER DEFAULT 50,
    trust           INTEGER DEFAULT 30,
    mood            TEXT DEFAULT '平静',
    guard           INTEGER DEFAULT 70,
    affinity_reason TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

-- ---------- messages ----------
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id       TEXT NOT NULL,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    rag_context      TEXT DEFAULT '',
    speaker          TEXT DEFAULT '',
    retracted        SMALLINT DEFAULT 0,
    reply_to_id      INTEGER DEFAULT NULL,
    reply_to_preview TEXT DEFAULT '',
    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- ---------- users ----------
CREATE TABLE IF NOT EXISTS users (
    id                     TEXT PRIMARY KEY,
    username               TEXT NOT NULL UNIQUE,
    password_hash          TEXT NOT NULL,
    is_admin               SMALLINT DEFAULT 0,
    is_disabled            SMALLINT DEFAULT 0,
    api_key                TEXT DEFAULT '',
    base_url               TEXT DEFAULT 'https://api.deepseek.com',
    model                  TEXT DEFAULT 'deepseek-v4-pro',
    avatar_data            TEXT DEFAULT '',
    banner_data            TEXT DEFAULT '',
    bio                    TEXT DEFAULT '',
    email                  TEXT DEFAULT '',
    email_verified         SMALLINT DEFAULT 0,
    profile_stats_visible  SMALLINT DEFAULT 1,
    cards_visible          SMALLINT NOT NULL DEFAULT 1,
    books_visible          SMALLINT NOT NULL DEFAULT 1,
    following_visible      SMALLINT NOT NULL DEFAULT 1,
    presence_visibility    TEXT NOT NULL DEFAULT 'friends',
    last_login_at          TEXT DEFAULT '',
    last_active_at         TEXT DEFAULT '',
    created_at             TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------- wechat_users ----------
CREATE TABLE IF NOT EXISTS wechat_users (
    openid     TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    card_id    TEXT REFERENCES cards(id),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------- invite_codes ----------
CREATE TABLE IF NOT EXISTS invite_codes (
    id         TEXT PRIMARY KEY,
    code       TEXT UNIQUE NOT NULL,
    created_by TEXT NOT NULL,
    used_by    TEXT DEFAULT NULL,
    used_at    TEXT DEFAULT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------- refresh_tokens ----------
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used       SMALLINT DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ---------- usage_stats ----------
CREATE TABLE IF NOT EXISTS usage_stats (
    id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           TEXT NOT NULL,
    action            TEXT NOT NULL,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    model             TEXT DEFAULT '',
    created_at        TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- ---------- verification_codes ----------
CREATE TABLE IF NOT EXISTS verification_codes (
    id         TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    code       TEXT NOT NULL,
    purpose    TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used       SMALLINT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------- card_likes ----------
CREATE TABLE IF NOT EXISTS card_likes (
    user_id    TEXT NOT NULL,
    card_id    TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, card_id),
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

-- ---------- group_sessions ----------
CREATE TABLE IF NOT EXISTS group_sessions (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL DEFAULT '',
    card_ids              TEXT NOT NULL DEFAULT '[]',
    user_id               TEXT NOT NULL DEFAULT '',
    deleted_at            TEXT DEFAULT '',
    user_persona_type     TEXT DEFAULT 'director',
    user_persona_card_id  TEXT DEFAULT '',
    user_persona_name     TEXT DEFAULT '',
    user_persona_desc     TEXT DEFAULT '',
    created_at            TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------- group_messages ----------
CREATE TABLE IF NOT EXISTS group_messages (
    id               INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_id         TEXT NOT NULL,
    speaker          TEXT NOT NULL DEFAULT '',
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    speaker_card_id  TEXT DEFAULT '',
    reply_to_id      INTEGER DEFAULT NULL,
    reply_to_preview TEXT DEFAULT '',
    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_group_messages_group_id ON group_messages(group_id);

-- ---------- card_comments ----------
CREATE TABLE IF NOT EXISTS card_comments (
    id                  TEXT PRIMARY KEY,
    card_id             TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    username            TEXT NOT NULL,
    content             TEXT NOT NULL,
    is_ai_reply         SMALLINT NOT NULL DEFAULT 0,
    ai_card_id          TEXT NOT NULL DEFAULT '',
    ai_version_label    TEXT NOT NULL DEFAULT '',
    reply_to_comment_id TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

-- ---------- user_follows ----------
CREATE TABLE IF NOT EXISTS user_follows (
    follower_id  TEXT NOT NULL,
    following_id TEXT NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (follower_id, following_id)
);

-- ---------- user_posts ----------
CREATE TABLE IF NOT EXISTS user_posts (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    content    TEXT NOT NULL,
    visibility TEXT DEFAULT 'public',
    images     TEXT DEFAULT '',
    card_id    TEXT DEFAULT '',
    likes      INTEGER DEFAULT 0,
    location   TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------- text_comments ----------
CREATE TABLE IF NOT EXISTS text_comments (
    id         TEXT PRIMARY KEY,
    text_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    username   TEXT NOT NULL,
    content    TEXT NOT NULL,
    parent_id  TEXT DEFAULT '',
    likes      INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (text_id) REFERENCES texts(id) ON DELETE CASCADE
);

-- ---------- text_comment_likes ----------
CREATE TABLE IF NOT EXISTS text_comment_likes (
    comment_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    PRIMARY KEY (comment_id, user_id)
);

-- ---------- direct_messages ----------
CREATE TABLE IF NOT EXISTS direct_messages (
    id          TEXT PRIMARY KEY,
    sender_id   TEXT NOT NULL,
    receiver_id TEXT NOT NULL,
    content     TEXT NOT NULL,
    is_read     SMALLINT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_receiver ON direct_messages(receiver_id, is_read);
CREATE INDEX IF NOT EXISTS idx_dm_conversation ON direct_messages(sender_id, receiver_id);

-- ---------- post_likes ----------
CREATE TABLE IF NOT EXISTS post_likes (
    user_id    TEXT NOT NULL,
    post_id    TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, post_id)
);

-- ---------- post_comments ----------
CREATE TABLE IF NOT EXISTS post_comments (
    id          TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    username    TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    ip_location TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ---------- card_versions ----------
CREATE TABLE IF NOT EXISTS card_versions (
    id                 TEXT PRIMARY KEY,
    card_id            TEXT NOT NULL,
    user_id            TEXT NOT NULL,
    version_num        INTEGER NOT NULL DEFAULT 1,
    publish_message    TEXT DEFAULT '',
    diff_json          TEXT DEFAULT '{}',
    card_json_snapshot TEXT DEFAULT '{}',
    created_at         TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_card_versions_card ON card_versions(card_id);

-- ---------- card_comment_reports ----------
CREATE TABLE IF NOT EXISTS card_comment_reports (
    id          TEXT PRIMARY KEY,
    comment_id  TEXT NOT NULL,
    card_id     TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ,
    resolver_id TEXT,
    FOREIGN KEY (comment_id) REFERENCES card_comments(id) ON DELETE CASCADE,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

-- ---------- config_changelog ----------
CREATE TABLE IF NOT EXISTS config_changelog (
    id             TEXT PRIMARY KEY,
    admin_id       TEXT NOT NULL,
    admin_username TEXT NOT NULL,
    field          TEXT NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------- review_log ----------
CREATE TABLE IF NOT EXISTS review_log (
    id         TEXT PRIMARY KEY,
    card_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    result     TEXT NOT NULL,
    reason     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------- featured_cards ----------
CREATE TABLE IF NOT EXISTS featured_cards (
    id         TEXT PRIMARY KEY,
    card_id    TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES cards(id)
);

-- ---------- message_reactions ----------
CREATE TABLE IF NOT EXISTS message_reactions (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id INTEGER NOT NULL,
    user_id    TEXT NOT NULL,
    emoji      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (message_id, user_id, emoji)
);

-- ---------- reading_progress ----------
CREATE TABLE IF NOT EXISTS reading_progress (
    user_id         TEXT NOT NULL,
    text_id         TEXT NOT NULL,
    progress        DOUBLE PRECISION DEFAULT 0,
    scroll_position INTEGER DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, text_id)
);

-- ---------- announcements ----------
CREATE TABLE IF NOT EXISTS announcements (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    is_active  SMALLINT NOT NULL DEFAULT 1,
    align      TEXT NOT NULL DEFAULT 'left',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------- 原 SQLite 001 的索引 ----------
CREATE INDEX IF NOT EXISTS idx_cards_text_id     ON cards(text_id);
CREATE INDEX IF NOT EXISTS idx_sessions_card_id  ON sessions(card_id);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_content  ON messages(content);

-- ---------- geo_block_log ----------
CREATE TABLE IF NOT EXISTS geo_block_log (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    TEXT NOT NULL,
    ip         TEXT NOT NULL DEFAULT '',
    base_url   TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
