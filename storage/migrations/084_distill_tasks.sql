-- Distillation task persistence: task record + per-chunk map results (resume support)
CREATE TABLE IF NOT EXISTS distill_tasks (
    task_id     TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    text_id     TEXT NOT NULL,
    character   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',
    progress_pct INTEGER NOT NULL DEFAULT 0,
    message     TEXT NOT NULL DEFAULT '',
    card_id     TEXT NOT NULL DEFAULT '',
    awakening   TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS distill_chunks (
    task_id     TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    result      TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, chunk_index)
);
