# 2026-05-17 TTS 文件缓存 + API 端点

### 操作摘要

- **做了什么**：EdgeTTSEngine 加 MD5 文件缓存，新建 `POST /api/tts/synthesize` 端点，deps 加 TTS 单例，server 挂载路由
- **为什么**：避免相同文本+语音重复调用 Edge TTS API，缓存命中从 1.1s 降至 0.01s
- **影响范围**：`speech/edge_tts_client.py`（重写）、`web/routers/tts.py`（新建）、`web/deps.py`、`web/server.py`

### 缓存策略

- 缓存键：`md5(voice:text)` → `.mp3`
- 缓存目录：`data/tts_cache/`
- 命中时直接 `read_bytes()`，未命中合成后 `write_bytes()`
- 语音短名（xiaoxiao/yunxi等）自动解析为完整 ID

### 验证结果

| 测试 | 结果 |
|------|------|
| 首次合成 | 200, 13680 bytes, 1.1s |
| 缓存命中 | 200, 13680 bytes, 0.010s (110x) |
| 切换语音 | 200, yunxi 男声 |
| 空文本 | 400 拒绝 |
