### 15:30 语音输入链路排查 — WAV header 盲切 bug 修复

- **做了什么**：逐段验证前端录音→ffmpeg→FunASR WebSocket 全链路，定位并修复 `voice.py` 中 WAV header 硬编码 44 字节盲切 bug
- **为什么**：ffmpeg webm→wav 转码会插入 LIST/INFO chunk，导致 header 长度变为 78 字节（非标准 44 字节）。原代码 `wav_data[44:]` 会将 34 字节元数据 `INFOISFT...` 注入 PCM 音频流，损坏音频数据
- **影响范围**：`web/routers/voice.py` — 三处修改
  - Line 240: `wav_data[44:]` 替换为 `wave.open()` 正确解析 WAV data chunk
  - Line 47/212: `open("config.yaml")` 替换为基于 `__file__` 的绝对路径（防止 CWD 错误时找不到配置文件）

### 链路验证结果

| 环节 | 状态 | 备注 |
|------|------|------|
| ffmpeg 二进制 | ✅ | `C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe` 可用 |
| FunASR WebSocket | ✅ | `ws://127.0.0.1:10095` 可达，模型加载完毕 |
| ffmpeg webm→wav 转码 | ✅ | 正常 |
| WAV header 大小 | ❌→✅ | 78 bytes，原 44-byte 假设错误，已修复 |
| config.yaml 路径 | ❌→✅ | 相对路径已改为 `__file__` 锚定 |
| FunASR 识别精度 | ✅ | 中文语音正常识别+标点 |
| 后端服务 | ⚠️ | 入口为 `web/server.py` 端口 7860（非 `web/main.py`） |
