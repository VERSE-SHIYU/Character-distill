-- Soft delete for cards: add deleted_at timestamp column
ALTER TABLE cards ADD COLUMN deleted_at TEXT;
