ALTER TABLE refresh_tokens ADD COLUMN used_at TEXT DEFAULT '';
ALTER TABLE refresh_tokens ADD COLUMN replaced_by TEXT DEFAULT '';
