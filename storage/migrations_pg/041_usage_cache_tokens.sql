-- 同 SQLite 106（PG 侧编号顺延）：缓存命中/未命中字段，NULL 表示厂商没回。
ALTER TABLE usage_stats ADD COLUMN IF NOT EXISTS prompt_cache_hit_tokens INTEGER;
ALTER TABLE usage_stats ADD COLUMN IF NOT EXISTS prompt_cache_miss_tokens INTEGER;
