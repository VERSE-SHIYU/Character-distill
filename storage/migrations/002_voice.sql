-- 002_voice.sql
-- Add voice_ref_json column to sessions table for voice cloning feature

ALTER TABLE sessions ADD COLUMN voice_ref_json TEXT DEFAULT '';
