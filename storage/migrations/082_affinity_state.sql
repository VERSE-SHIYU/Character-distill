ALTER TABLE sessions ADD COLUMN affinity_state TEXT DEFAULT '';
ALTER TABLE sessions ADD COLUMN affinity_initialized INTEGER DEFAULT 0;
