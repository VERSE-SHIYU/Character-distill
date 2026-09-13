-- 同 SQLite 085：chunk_count = 本次记账聚合了几次 LLM 调用，**不是**文本分片数。
-- 续跑命中/重试会让它与分片数不一致。非聚合记录为 NULL。
ALTER TABLE usage_stats ADD COLUMN IF NOT EXISTS chunk_count INTEGER;
