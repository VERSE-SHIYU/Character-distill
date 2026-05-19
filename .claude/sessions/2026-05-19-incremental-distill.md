### 15:00 蒸馏增量更新重构 — 移除RAG依赖，支持100万字文本

- **做了什么**：按用户指定方案，将蒸馏从"关键词截取+RAG一次蒸馏"改为"逐块增量更新"策略，分5个切片完成
- **为什么**：
  1. 问题1（截断丢失）：原 max_input_chars=30000 对100万字丢失97%内容；增量更新逐块处理，每块仅含角色相关内容
  2. 问题3（RAG瓶颈）：蒸馏阶段移除RAG依赖，RAG仅保留在对话阶段（chat_engine.py不动）
  3. 问题4（进度不透明）：SSE推送逐块进度，前端显示"正在分析第X/Y段"
  4. 问题2（Codified Profile）留待第二期
- **影响范围**：
  - `config.yaml` — distill.max_input_chars删除，新增chunk_size:3000 + max_profile_len:2000
  - `core/distiller.py` — 删除`_extract_character_paragraphs`和RAG导入；简化`distill()`/`distill_stream()`（移除rag参数）；新增`_split_chunks()`、`distill_incremental()`、`distill_incremental_stream()`
  - `core/text_manager.py` — `get_or_distill()`移除rag/all_chars参数，改用distill_incremental；`save_distilled_card()`移除rag参数；`distill_all()`改用distill_incremental
  - `web/routers/distill.py` — 删除RAG构建段；SSE路由改用distill_incremental_stream，推送进度事件；移除未使用的RAGEngine/get_rag_config导入
  - `web/frontend/src/api/client.js` — streamSSE中onStatus改为传递完整payload（支持进度事件携带current/total）
  - `web/frontend/src/store/useAppStore.js` — distillCharacter的onStatus增加analyzing进度处理

### 验证结果

| 检查项 | 期望 | 实际 |
|--------|------|------|
| max_input_chars引用 | 0 | 0 |
| _extract_character_paragraphs引用 | 0 | 0 |
| RAGEngine导入（distill.py） | 0 | 0 |
| get_rag_config导入（distill.py） | 0 | 0 |
| get_or_distill含rag参数调用 | 0 | 0 |
| save_distilled_card含rag参数调用 | 0 | 0 |
| Python语法检查 | 通过 | ✅ |
| 前端build | 成功 | ✅ 283ms |
