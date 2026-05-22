# 2026-05-21 五连修 Bug

### 修复概览
- **Bug 1**：distiller.py `_map_system_prompt_chat` f-string 未插值 — `{character_name}` 字面量发给 LLM
- **Bug 2**：误报 — context_engine.py 无 `@staticmethod` + `self` 问题，当前代码正确
- **Bug 3**：asyncio.run() 在已有 event loop 线程中崩溃 — `_try_record_usage` 改用 daemon thread + `new_event_loop()`
- **Bug 4**：async_chat 并发写 last_usage 数据竞争 — 移除 async_chat 中的 `self.last_usage` 赋值
- **Bug 5**：`_tasks` 字典无清理内存泄漏 — GET task 终态时自动 pop
- **Bug 6**：Layer 0 日期去重去掉 `[date]` 前缀 → `_split_chunks_chat` 按天分组失效 — 保留所有行的日期前缀

### 影响范围
- `core/distiller.py` — Bug 1：line 151,153 → f-string
- `core/chat_engine.py` — Bug 3：`_try_record_usage` 新线程 + `new_event_loop`；`_evaluate_affinity._do` `asyncio.run()` → `new_event_loop()`
- `adapters/llm_adapter.py` — Bug 4：`async_chat` 返回 `(result, usage)` 元组，调用方聚合，消除共享状态竞争
- `core/distiller.py` — Bug 4 续：`_try_record_usage` 接受可选 `usage` 参数 + 顺带修 `asyncio.run()` → thread+`new_event_loop()`
- `web/routers/distill.py` — Bug 5：GET task 终态清理
- `core/chat_preprocessor.py` — Bug 6：保留所有行日期前缀 + 移除 dead `seen_dates`

### 验证
- ✅ 全部 5 个文件 Python 语法检查通过

---

### 2026-05-22 第二轮：六连修逻辑 Bug

- **Bug 1**：chat()/chat_stream() 历史注入两次 — `_build_history` 嵌入 system prompt + `llm_history` 再次传入。修复：只传当前用户消息，历史统一由 system prompt 管理。
- **Bug 2**：`_do_reduce` asyncio.run() 在 distill_incremental_stream event loop 中崩溃。修复：detect running loop → serial fallback。
- **Bug 3**：sync `chat()`/`chat_stream()` 写共享 `last_usage`，`_try_record_usage` 读到可能被覆盖的值。修复：LLM 调用后立即捕获 `last_usage` 显式传入。
- **Bug 4**：`_compute_initial_affinity` 子串匹配单字误命中（"明" 误中 "明朝学生"）。修复：要求 ≥2 字符才做子串匹配。
- **Bug 5**：Reduce batch 结果列表推导静默丢弃空 entry。修复：for 循环 + print warning。
- **Bug 6**：`chat()` 返回 `tuple[str, str]` 但始终 `return response, ""`。修复：返回 `str`，更新 `web/app.py` 调用点。

### 影响范围
- `core/chat_engine.py` — Bug 1/4/6：去重历史、关系匹配 guard、返回类型
- `core/distiller.py` — Bug 2/3/5：event loop 检测、last_usage 捕获、batch warning
- `web/app.py` — Bug 6：`resp, _` → `resp`

### 验证
- ✅ 全部 3 个文件 Python 语法检查通过
