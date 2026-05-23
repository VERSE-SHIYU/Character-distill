-- 027_voice_to_cards.sql
-- Move voice_ref_json from sessions to cards so it's tied to the character card, not the session

ALTER TABLE cards ADD COLUMN voice_ref_json TEXT DEFAULT '';

-- Migrate existing data: for each card, take the newest session's voice_ref_json
UPDATE cards SET voice_ref_json = (
    SELECT voice_ref_json FROM sessions
    WHERE sessions.card_id = cards.id AND sessions.voice_ref_json IS NOT NULL AND sessions.voice_ref_json != ''
    ORDER BY sessions.updated_at DESC LIMIT 1
) WHERE EXISTS (
    SELECT 1 FROM sessions
    WHERE sessions.card_id = cards.id AND sessions.voice_ref_json IS NOT NULL AND sessions.voice_ref_json != ''
);
