# 2026-05-17 RAG 角色标注与按角色过滤查询

### HH:MM 操作摘要

- **做了什么**：在 `core/rag.py` 的 `RAGEngine` 中新增 `_tag_characters` 方法，修改 `index` 和 `query` 支持角色标注和按角色过滤
- **为什么**：让 RAG 引擎在索引时标注每段文本出场的角色，查询时可按角色名过滤，只返回该角色真正出场的片段
- **影响范围**：`core/rag.py`（新增 1 个私有方法，`index` 和 `query` 各加 1 个可选参数，完全向后兼容）

### 关键决策

- `_tag_characters` 返回 `list[str]`（非逗号分隔字符串），因 ChromaDB 1.5.9 的 `$contains` 操作符仅对数组类型 metadata 生效
- `all_characters` 为 None 时不传 metadatas，因 ChromaDB 1.5 禁止空数组作为 metadata 值
- `character_name` 为 None 时不传 where，行为与旧版完全一致
- 子串匹配忽略大小写，一个 chunk 中同一角色多个别名命中只记一次
