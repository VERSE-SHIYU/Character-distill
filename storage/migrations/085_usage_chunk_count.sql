-- chunk_count = 本次记账**聚合了几次 LLM 调用**（Map 阶段按阶段汇总成一条时填），
-- **不是**「文本切了几片」。续跑命中分片或失败重试会让两者不一致：
-- 分片可能被跳过（零调用）或重试多次（多调用）。非聚合记录为 NULL。
ALTER TABLE usage_stats ADD COLUMN chunk_count INTEGER;
