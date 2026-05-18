### 16:15 GPT-SoVITS 音色克隆功能实现

- **做了什么**：实现参考音频上传/绑定角色卡 → 对话合成时自动选择 GPT-SoVITS（有参考音频）或 Edge TTS（无参考音频/服务不可用）的双引擎语音合成
- **为什么**：P3 功能需求，利用已有 VoiceCloneClient 实现音色克隆，不可用时自动降级不阻塞语音功能
- **影响范围**：
  - **修改** `web/routers/voice.py` — 三处核心改动：
    - 替换 3 个 ref-audio 501 stub 为真实实现（GET/POST/DELETE，含文件校验、20MB 限制、路径存 DB）
    - `voice_synthesize` 改为双引擎：有 card_id + 参考音频 + GPT-SoVITS 可用 → VoiceCloneClient；否则 → Edge TTS
    - `voice_status` 的 `gptsovits` 从硬编码 `False` 改为真实 `health_check()` 检测
    - 3 处 `open("config.yaml")` 改为 repo-root 绝对路径
  - **修改** `web/frontend/src/store/useAppStore.js` — `_synthesizeVoiceReply` body 添加 `card_id`
  - **修改** `web/frontend/src/components/ChatArea.jsx` — `playTTS` body 添加 `card_id`

### 设计决策
- **不复用 `speech/gptsovits_client.py`**（用户指定名），使用已有 `speech/voice_clone.py` 的 `VoiceCloneClient`（API 签名一致，含缓存）
- **参考音频参数名**：表单字段 `ref_text` 替代原 stub 的 `prompt_text`（更语义化）
- **存储复用**：`update_session_voice_ref` / `get_session_voice_ref` 已在 `sqlite_store.py` 实现，无需迁移

### 验证结果

| 检查项 | 状态 |
|--------|------|
| voice.py 语法 | ✅ py_compile 通过 |
| 501 stub 残留 | ✅ 0 处 |
| voice router import | ✅ 通过 |
| 双引擎引用数 | ✅ 17 处（>=4） |
| 前端 card_id 注入 | ✅ store: 8, ChatArea: 2 |
| httpx 依赖 | ✅ 已存在 |
| 前端 build | ✅ 648ms 成功 |
