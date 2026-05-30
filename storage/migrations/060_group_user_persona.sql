ALTER TABLE group_sessions ADD COLUMN user_persona_type TEXT DEFAULT 'director';
ALTER TABLE group_sessions ADD COLUMN user_persona_card_id TEXT DEFAULT '';
ALTER TABLE group_sessions ADD COLUMN user_persona_name TEXT DEFAULT '';
ALTER TABLE group_sessions ADD COLUMN user_persona_desc TEXT DEFAULT '';
