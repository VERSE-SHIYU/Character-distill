### 16:30 三层防御 meta tensor 终极修复
- **做了什么**：(1) Layer 2 — 在 `distiller.py` 的 `identify_characters` 和 `distill` 方法顶部加入 env/torch 守卫；(2) Layer 2 — 在 `text_manager.py` 的 `_create_session`（实际触发 `SentenceTransformer` 加载的调用点）顶部加入守卫；(3) Layer 3 — 在 `embeddings.py` 的 `create_safe_embedding_fn` 内部，模型加载前再次强化 env vars，加载后增加 meta-device 检测与 `to_empty` 回退
- **为什么**：之前的补丁（Layer 1 `fix_meta_tensor.py`）只在进程入口设置 env vars + torch defaults + `nn.Module.to` monkey-patch，但蒸馏时 `_create_session` → `RAGEngine` → `SentenceTransformer` 的实际调用链路可能因 `asyncio.to_thread` 线程池或 `accelerate` 内部逻辑而绕过补丁。现在在方法级（Layer 2）和模型加载调用点（Layer 3）都放置了防御代码
- **影响范围**：
  - `core/distiller.py` — `identify_characters` 和 `distill` 方法顶部新增 env/torch 守卫
  - `core/text_manager.py` — `_create_session` 方法顶部新增 env/torch 守卫（最关键：这是实际模型加载调用点）
  - `core/embeddings.py` — `create_safe_embedding_fn` 内部新增 pre-call env var 强化 + post-load meta 检查 + `to_empty` 回退
