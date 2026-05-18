### 09:30 实现语音输入功能（FunASR ASR 全栈）— 14:30 部署+调试完成

- **做了什么**：前端录音 → 后端 FunASR WebSocket 语音识别 → 文字填入输入框 的完整闭环
- **为什么**：原有 ASR 端点为 501 stub，前端录音按钮已存在但无后端支持
- **影响范围**：
  - **新增** `speech/funasr_client.py` — WebSocket 客户端
  - **新增** `speech/funasr_server.py` — 本地 FunASR WebSocket 服务（替代 Docker，因阿里云镜像仓库不可达）
  - **修改** `web/routers/voice.py` — `/asr` 从 501 stub 替换为真实实现；`/status` funasr 字段真实检测；ffmpeg 使用全路径
  - **修改** `config.yaml` — funasr_url 协议 http:// → ws://
  - **修改** `requirements.txt` — 添加 websockets>=12.0
  - **修改** `web/frontend/src/store/useAppStore.js` — sendVoiceMessage 返回文字而非自动发送
  - **修改** `web/frontend/src/components/ChatArea.jsx` — ChatInput 接收返回文字填入 textarea

## 部署调试记录

### Docker 不可达
- 阿里云 registry.cn-hangzhou.aliyuncs.com 从 Docker Desktop 无法连接（connectex 错误）
- 改用 `funasr` Python 包 + `speech/funasr_server.py` 本地 WebSocket 服务

### funasr_server.py 调试过程
1. **UnicodeDecodeError**: `json.loads(bytes)` 对 PCM 二进制数据抛 UnicodeDecodeError 而非 JSONDecodeError → 加 `isinstance(message, bytes)` 优先判断
2. **keepalive ping timeout**: `model.generate()` 同步阻塞 asyncio 事件循环 → 改用 `asyncio.to_thread()` 放入线程池
3. **模型加载阻塞端口监听**: 模型在 `start()` 中同步加载，端口迟迟不绑定 → 改为先启动 WebSocket server，模型用 `asyncio.create_task` 后台加载 + `asyncio.Event` 信号
4. **ffmpeg 找不到**: funasr 内部调 `subprocess.run(["ffmpeg"])` 但 PATH 无 ffmpeg → `_load_model()` 中 `os.environ.setdefault("PATH", ...)` 添加 ffmpeg 路径

### ffmpeg 安装
- winget/scoop/choco 均不可用 → curl 下载 gyan.dev 构建版 → 解压到 C:\ffmpeg → 通过 PowerShell 添加到用户 PATH

### 最终验证
- TTS "今天天气真好，我们一起去公园散步吧" → ffmpeg 转码 → FunASR WebSocket → **"今天天气真好，我们一起去公园散步吧。"** ✅ 完美识别+标点
- `FunASRClient.is_available()` → True ✅
- 前端 build 通过 ✅

### 启动方式
```bash
# 1. 启动 FunASR 服务
python speech/funasr_server.py &

# 2. 启动后端
cd web && python main.py  # 或 uvicorn

# 3. 前端 build 或 dev server
cd web/frontend && npx vite build  # 或 npm run dev
```
