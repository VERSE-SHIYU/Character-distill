# 2026-05-17 RAG 重建索引 + 聊天过滤集成验证

### 操作摘要

- **做了什么**：新增 `POST /api/distill/reindex/{text_id}` 端点，ChatEngine 加 RAG 过滤日志，运行 MDZS 角色过滤冒烟测试
- **为什么**：旧 session 的 RAG collection 无角色 metadata，需要重建索引；聊天层需要可见的过滤日志来验证 where 是否生效
- **影响范围**：`web/routers/distill.py`（新增 reindex 路由）、`core/chat_engine.py`（chat/chat_stream 加 `print` 日志）

### 验证结果

- `POST /api/distill/reindex/{text_id}` 路由已注册 ✓
- 冒烟测试：江澄/魏无羡/蓝忘机角色过滤正确命中 ✓
- ChatEngine 日志输出：`[ChatEngine] RAG where filter: {'characters': {'$contains': '江澄'}}` ✓
- 向后兼容：无 `all_characters` 时日志输出 `None`，行为不变 ✓
