-- DeepSeek 的 prompt_cache_hit_tokens / prompt_cache_miss_tokens（见
-- https://api-docs.deepseek.com/guides/kv_cache ）。别的厂商不回这两项 → 留 NULL，
-- **不写 0**：0 是「确实一次没命中」，与「没有这项数据」必须可区分。
ALTER TABLE usage_stats ADD COLUMN prompt_cache_hit_tokens INTEGER;
ALTER TABLE usage_stats ADD COLUMN prompt_cache_miss_tokens INTEGER;
