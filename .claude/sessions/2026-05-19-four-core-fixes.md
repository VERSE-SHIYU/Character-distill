### 17:00 修复四个核心问题：蒸馏超时、人物失真、历史卡顿、撤回无效

- **做了什么**：按用户指定的执行顺序，逐文件逐行精确修复四个根因问题
- **为什么**：
  1. 蒸馏超时：前端默认3分钟不足以完成LLM调用；后端8万字输入过多导致LLM处理缓慢
  2. 人物失真：`_extract_character_paragraphs` 硬截断在任意字节处，导致语义断裂；配角出现在文本后半段时截取不到有效内容
  3. 历史卡顿：`selectCard` 同步等待 session 创建后才更新 UI，导致点击历史角色后界面冻结数秒
  4. 撤回无效：服务重启后 `resume_session` 未重建 `message_ids`，导致 `revoke` 中 `msg_ids.index()` 失败后 `idx=0` 误删全部消息
- **影响范围**：
  - `web/frontend/src/api/client.js` — fetchWithTimeout 默认 180s→600s，postJSON 透传 ms 参数
  - `config.yaml` — distill.max_input_chars 80000→30000
  - `core/distiller.py` — _extract_character_paragraphs 段落边界安全截断 + 后半段角色 fallback
  - `web/frontend/src/store/useAppStore.js` — selectCard 乐观更新（立即展示UI+后台建session）
  - `web/routers/history.py` — resume_session 重建 sessions[session_id]["message_ids"]
  - `web/routers/chat.py` — revoke 中 ValueError 不再默认为 idx=0，改为仅DB删除
  - 前端build：成功 ✅ 473ms
