CREATE TABLE IF NOT EXISTS group_affinity (
    group_id        TEXT NOT NULL,
    card_id         TEXT NOT NULL,
    affinity        INTEGER DEFAULT 50,
    trust           INTEGER DEFAULT 30,
    mood            TEXT DEFAULT '平静',
    guard           INTEGER DEFAULT 70,
    affinity_reason TEXT DEFAULT '',
    PRIMARY KEY (group_id, card_id)
);
