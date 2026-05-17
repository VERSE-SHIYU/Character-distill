# 角色模拟器 Character Simulator

> 上传任何文本（小说、聊天记录、人物描写），自动蒸馏出角色人格，跟他沉浸式对话。

## 功能

- **角色蒸馏**：从文本自动提取性格、说话风格、价值观、内在矛盾、人际关系
- **角色感知段落抽取**：蒸馏时自动定位角色相关段落，聚焦原文证据
- **沉浸对话**：以角色口吻回复，RAG 检索原文保证设定一致性
- **角色标记 RAG**：向量索引标注角色，对话时按角色名过滤检索上下文
- **对话前身份设定**：点击"开始对话"先弹出模态框，设定你在故事中的扮演角色
- **自定义头像**：角色卡和聊天界面均支持上传/更换头像，IndexedDB 持久化
- **聊天内换头像**：聊天顶栏头像叠加相机图标，点击即时更换
- **多格式导入**：支持 .txt / .md / .json / .csv / .log / .pdf / .docx，最大 100MB
- **文本元数据**：上传时填写标题和描述，列表展示
- **对话自动摘要**：长对话自动折叠旧消息，防止记忆溢出
- **别名合并**：自动识别"汪东城"和"大东"是同一人
- **SillyTavern v2 导出**：角色卡一键导出为兼容 JSON
- **TTS 语音合成**：Edge TTS 引擎 (晓晓/云希/晓伊/云扬)，消息气泡一键播放
- **自定义音色库**：上传人声样本创建私人音色，管理/试听/删除
- **音色克隆**：GPT-SoVITS 参考音频上传，角色语音回复
- **语音输入**：FunASR 语音识别，按住录音发送消息
- **毛玻璃 UI**：全局 glassmorphism 设计，浅色/深色主题
- **PWA 支持**：manifest + Service Worker，可安装到桌面
- **面包屑导航**：侧边栏实时显示当前位置路径
- **文件缓存**：TTS 合成结果 MD5 缓存，命中 0.01s（110x 加速）
- **SQLite 持久化**：角色卡、对话历史、文本库、音色引用全持久化

## 快速开始

```bash
git clone <repo>
cd Character-distill

# 安装依赖（Python 3.12+）
py -3.12 -m pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 构建前端（生产托管）
cd web/frontend
npm install
npm run build
cd ../..

# 启动服务
py -3.12 -m uvicorn web.server:app --host 0.0.0.0 --port 7860
# 浏览器打开 http://localhost:7860

# 开发模式（前端热更新）
cd web/frontend && npm run dev
```

## 项目结构

```
Character-distill/
├── adapters/
│   └── llm_adapter.py          # DeepSeek API 封装（OpenAI 兼容格式）
├── core/
│   ├── fix_meta_tensor.py      # 全局 meta tensor 防御（三层守卫）
│   ├── embeddings.py           # 安全 embedding 模型工厂（唯一入口）
│   ├── schema.py               # CharacterCard Pydantic 模型
│   ├── distiller.py            # 角色蒸馏引擎（含角色感知段落抽取）
│   ├── rag.py                  # 原文向量检索（ChromaDB + 角色标记过滤）
│   ├── chat_engine.py          # 对话引擎（system prompt + RAG + history）
│   ├── export.py               # SillyTavern v2 JSON 导出
│   └── text_manager.py         # 文本/会话生命周期管理
├── speech/
│   ├── tts_engine.py           # TTS 抽象接口
│   ├── edge_tts_client.py      # Edge TTS 客户端（MD5 文件缓存）
│   ├── voice_clone.py          # GPT-SoVITS 音色克隆客户端
│   └── asr_client.py           # FunASR 语音识别客户端
├── storage/
│   ├── base.py                 # 存储抽象层
│   ├── sqlite_store.py         # SQLite 实现（角色卡/对话/文本）
│   └── migrations/             # 数据库迁移
├── web/
│   ├── server.py               # FastAPI 入口 + 静态文件托管
│   ├── app.py                  # Gradio 界面（备用）
│   ├── deps.py                 # FastAPI 依赖注入（单例管理）
│   ├── routers/
│   │   ├── chat.py             # 对话 API（含流式 SSE）
│   │   ├── distill.py          # 蒸馏 + 导出 API
│   │   ├── history.py          # 历史管理 API
│   │   ├── text.py             # 文本上传 API（含元数据）
│   │   ├── tts.py              # TTS 合成 API
│   │   ├── voice.py            # 音色库 + 音色克隆 + ASR API
│   │   ├── wechat.py           # 微信接入 API
│   │   └── wechat_utils.py     # 微信加解密工具
│   ├── frontend/
│   │   ├── src/
│   │   │   ├── components/
│   │   │   │   ├── Sidebar.jsx         # 侧边栏（导航+面包屑+角色卡）
│   │   │   │   ├── HomePage.jsx        # 主页
│   │   │   │   ├── TextPanel.jsx       # 文本管理（上传+元数据模态框）
│   │   │   │   ├── CharCard.jsx        # 角色管理（列表+蒸馏+详情）
│   │   │   │   ├── ChatArea.jsx        # 聊天（身份栏+TTS+录音+头像编辑）
│   │   │   │   ├── HistoryPanel.jsx    # 历史会话
│   │   │   │   ├── SettingsPanel.jsx   # 设置（主题+音色+音色克隆）
│   │   │   │   ├── VoicePanel.jsx      # 音色管理（上传+试听+删除）
│   │   │   │   ├── RoleSetupModal.jsx  # 对话前身份设定模态框
│   │   │   │   └── common/             # Avatar, Loading, ErrorBox
│   │   │   ├── store/
│   │   │   │   ├── useAppStore.js      # Zustand 全局状态
│   │   │   │   └── db.js               # IndexedDB（头像持久化）
│   │   │   ├── api/
│   │   │   │   └── client.js           # HTTP/SSE 客户端
│   │   │   └── utils/
│   │   │       └── theme.js            # 主题切换
│   │   ├── public/
│   │   │   ├── manifest.json           # PWA manifest
│   │   │   ├── sw.js                   # Service Worker
│   │   │   ├── icon-192.png
│   │   │   └── icon-512.png
│   │   ├── index.html
│   │   ├── vite.config.js
│   │   └── package.json
│   └── static/
│       └── _deprecated/        # 旧版 CDN 前端（已废弃）
├── tests/
│   ├── test_chat.py
│   ├── test_rag.py
│   ├── test_distill.py
│   ├── test_connection.py
│   └── test_integration.py
├── data/                       # 运行时数据（自动创建）
│   ├── character_sim.db        # SQLite 数据库
│   ├── tts_cache/              # TTS 缓存目录
│   ├── voice_cache/            # 音色克隆缓存
│   ├── voice_library/          # 自定义音色库
│   └── voices/                 # 参考音频
├── config.yaml                 # 模型/RAG/蒸馏/存储/语音配置
├── requirements.txt
├── .env.example
└── README.md
```

## 架构流程

```
用户输入文本
    │
    ▼
adapters/llm_adapter.py         ← DeepSeek API（OpenAI 兼容）
    │
    ├─→ core/distiller.py       ← 角色感知段落抽取 → LLM 蒸馏 → CharacterCard
    │       │
    │       ▼
    │   core/schema.py          ← Pydantic 结构化校验
    │
    ├─→ core/rag.py             ← 分块 + 角色标记 → ChromaDB 向量索引
    │       │
    │       ▼
    │   core/embeddings.py      ← 安全 SentenceTransformer 工厂（CPU 锁定）
    │
    ├─→ core/chat_engine.py     ← 角色卡 system prompt + 角色过滤 RAG + history
    │       │
    │       ▼
    │   LLM → 角色回复
    │
    ├─→ core/export.py          ← CharacterCard → SillyTavern v2 JSON
    │
    ├─→ speech/edge_tts_client.py  ← Edge TTS 语音合成 + MD5 缓存
    │
    ├─→ speech/voice_clone.py   ← GPT-SoVITS 音色克隆（角色语音回复）
    │
    └─→ speech/asr_client.py    ← FunASR 语音识别（语音输入）
```

## Meta Tensor 防御

项目使用三层防御策略解决 PyTorch `meta` device 问题（accelerate 在 CPU 环境将模型路由到 meta 设备导致的 "Cannot copy out of meta tensor" 错误）：

1. **Layer 1** — `core/fix_meta_tensor.py`：进程级 env vars + `torch.set_default_device("cpu")` + `nn.Module.to` monkey-patch，在 `server.py` 最早的 import 处执行
2. **Layer 2** — `distiller.py` / `text_manager.py` 方法级守卫：在每个可能触发模型加载的方法入口重新设置 env vars 和 torch 默认设备
3. **Layer 3** — `embeddings.py` 调用点防御：`SentenceTransformer` 加载前强化 env vars，加载后检测 meta device 并 `to_empty` 回退

## API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/text/upload` | POST | 上传文本文件（含标题、描述元数据） |
| `/api/text/list` | GET | 文本列表 |
| `/api/text/{id}` | DELETE | 删除文本 |
| `/api/distill/identify` | POST | 识别文本中角色 |
| `/api/distill/run` | POST | 蒸馏角色卡 + 创建会话 |
| `/api/distill/cards/{text_id}` | GET | 角色卡列表 |
| `/api/distill/cards/{id}/export` | GET | 导出 SillyTavern v2 JSON |
| `/api/distill/start_session` | POST | 为已有角色卡创建会话 |
| `/api/distill/reindex/{text_id}` | POST | 重建 RAG 角色标记索引 |
| `/api/chat/send` | POST | 发送消息（SSE 流式 / JSON） |
| `/api/chat/revoke` | POST | 撤回消息 |
| `/api/chat/reset` | POST | 重置对话 |
| `/api/history/list` | GET | 会话历史列表（分页） |
| `/api/history/{id}` | GET | 会话详情 + 消息 |
| `/api/history/{id}/resume` | POST | 恢复会话（重建 ChatEngine） |
| `/api/history/{id}` | DELETE | 删除会话 |
| `/api/history/{id}/export` | GET | 导出会话（JSON/TXT） |
| `/api/tts/synthesize` | POST | Edge TTS 语音合成 |
| `/api/voice/status` | GET | 语音服务状态 + 预设音色列表 |
| `/api/voice/list` | GET | 全部音色（预设 + 自定义） |
| `/api/voice/upload` | POST | 上传自定义音色样本 |
| `/api/voice/{voice_id}` | DELETE | 删除自定义音色 |
| `/api/voice/preview-audio/{voice_id}` | GET | 试听音色（Edge TTS / 原始文件） |
| `/api/voice/synthesize` | POST | Edge TTS 语音合成 |
| `/api/voice/ref-audio/{card_id}` | GET | 角色参考音频（501 未实现） |
| `/api/voice/ref-audio/upload` | POST | 上传角色参考音频（501 未实现） |
| `/api/voice/ref-audio/{card_id}` | DELETE | 删除角色参考音频（501 未实现） |
| `/api/voice/asr` | POST | 语音转文字（501 未实现） |
| `/api/settings/config` | GET | 后端配置信息（只读） |
| `/api/wechat/*` | * | 微信公众号接入 |

## 配置说明

`config.yaml` 关键配置：

```yaml
llm:
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"
  temperature: 0.7
  max_tokens: 4096

rag:
  chunk_size: 500          # 文本分块大小
  chunk_overlap: 50        # 分块重叠
  top_k: 3                 # 检索返回数量
  embedding_model: "all-MiniLM-L6-v2"

distill:
  max_input_chars: 80000   # 蒸馏输入上限（约 8 万字）

storage:
  type: sqlite
  path: data/character_sim.db

voice:
  enabled: false           # GPT-SoVITS 音色克隆开关
  gptsovits_url: "http://127.0.0.1:9880"
  funasr_url: "http://127.0.0.1:10095"
  ref_audio_min_seconds: 30
  ref_audio_max_seconds: 60
  default_speed: 1.0
```

## 蒸馏方法论

蒸馏 prompt 设计参考：
- **Nuwa-skill**：认知框架分层提取（身份→性格→价值观→矛盾）
- **BookWorld**（ACL 2025）：小说角色一致性建模

核心原则：
1. **跨场景验证**：一个特质必须在 2 个以上不同场景出现才能写入
2. **有预测力**：提取的特质能预测此人在新情境下的反应
3. **保留矛盾**：矛盾是真实人格的标志，不准美化、不准调和
4. **忠于原文**：他是什么样就是什么样，不添加、不美化、不删减

## 对话机制

每次用户发送消息：
1. **RAG 检索**：用消息在原文向量库中找最相关片段（按角色名过滤）
2. **构建 prompt**：角色卡全维度 + RAG 原文 + 多轮历史 → system prompt
3. **LLM 生成**：DeepSeek 以角色身份流式回复
4. **行为约束**：永不承认是 AI、保持口癖、表现矛盾、不编造

## 语音功能

### Edge TTS（内置）
- **引擎**：Microsoft Edge TTS（`edge-tts` 库）
- **音色**：晓晓（女活泼）、云希（男青年）、晓伊（女温柔）、云扬（男新闻）
- **缓存**：相同文本+音色 MD5 缓存到 `data/tts_cache/`，命中 ~0.01s
- **播放**：角色消息气泡悬停显示播放按钮，全局单例播放

### 自定义音色库
- **上传**：wav/mp3 人声样本 + 名称，存入音色库
- **管理**：列表展示（名称、时长、日期），试听原始音频，删除
- **音色选择**：设置面板下拉框合并内置和自定义音色

### 音色克隆（GPT-SoVITS）
- **参考音频**：为角色上传 30-60 秒参考音频 + 文字标注
- **语音回复**：角色消息使用克隆音色朗读
- **语音输入**：按住录音按钮 → FunASR 转文字 → 自动发送
- **状态检测**：GPT-SoVITS 和 FunASR 健康检查，UI 自适应显示

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM | DeepSeek V4 Pro（OpenAI 兼容 API） |
| 向量检索 | sentence-transformers + ChromaDB |
| 后端 | FastAPI + Uvicorn |
| 前端 | React 18 + Vite + Zustand |
| 持久化 | SQLite（aiosqlite）+ IndexedDB（idb） |
| 数据校验 | Pydantic V2 |
| TTS | Microsoft Edge TTS + GPT-SoVITS |
| ASR | FunASR |
| 设计 | 毛玻璃 glassmorphism（light/dark） |
| 备用界面 | Gradio |

## 路线图

- [x] React Vite 前端（替换 CDN/Babel）
- [x] FastAPI 路由拆分 + 依赖注入
- [x] SQLite 持久化
- [x] 角色别名合并
- [x] 自定义角色头像（IndexedDB）
- [x] 聊天内更换头像
- [x] 对话前身份设定模态框
- [x] 多格式文本导入（含 PDF/DOCX）
- [x] 文本元数据（标题+描述）
- [x] 对话自动摘要
- [x] 角色感知段落抽取
- [x] 角色标记 RAG + 过滤查询
- [x] SillyTavern v2 角色卡导出
- [x] Edge TTS 语音合成 + 缓存
- [x] 自定义音色库（上传/试听/管理）
- [x] GPT-SoVITS 音色克隆 + 语音回复
- [x] FunASR 语音输入
- [x] 毛玻璃 UI + 面包屑导航
- [x] PWA 可安装
- [x] Meta tensor 三层防御
- [ ] 局势分析（好感度 / 成功概率实时计算）
- [ ] 多角色同时蒸馏 + 群聊模式
- [ ] 微信接入

## License

MIT
