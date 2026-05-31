ALTER TABLE card_comments ADD COLUMN is_ai_reply INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_comments ADD COLUMN ai_card_id TEXT NOT NULL DEFAULT '';
ALTER TABLE card_comments ADD COLUMN ai_version_label TEXT NOT NULL DEFAULT '';
ALTER TABLE card_comments ADD COLUMN reply_to_comment_id TEXT NOT NULL DEFAULT '';
