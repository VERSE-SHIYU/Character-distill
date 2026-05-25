### 23:00 七项严重缺陷修复（安全 + 数据一致性）
- **做了什么**：
  1. **鉴权绕过** CRITICAL: `_create_session` 存 `user_id`，`_ensure_session` 内存命中后校验 ownership；防止 URL 泄漏导致会话劫持
  2. **角色卡污染**: `get_or_distill` 不再直接改 `card.first_message`，改在 `result` dict 上覆写，避免 LLM 动态 opening 污染 DB
  3. **summary 注入 history**: `start_session` 恢复旧会话消息时加 `if m["role"] not in ("user", "char"): continue` 过滤
  4. **last_usage 数据竞争**: `_try_record_usage` 改为传参 `usage`，在 LLM 调用后立即捕获 `self.llm.last_usage`
  5. **session_id 碰撞**: `_create_session` 从 `hashlib.md5` 改为 `uuid.uuid4().hex[:12]`
  6. **LLM cache 失效**: 已确认 `update_api_config` 调用 `clear_user_llm_cache`，无需修复
  7. **all_characters aliases 为空**: 新增 `_build_all_characters()` 从 `get_characters` 缓存合并 aliases，替换所有构造点
- **影响范围**：core/text_manager.py, core/chat_engine.py, web/routers/chat.py, web/routers/distill.py
