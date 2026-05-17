# 角色模拟器 Character Simulator

> 上传任何文本（小说、聊天记录、人物描写），自动蒸馏出角色人格，跟他沉浸式对话。

## 功能

- **角色蒸馏**：从文本自动提取性格、说话风格、价值观、内在矛盾、人际关系
- **角色感知段落抽取**：蒸馏时自动定位角色相关段落，聚焦原文证据
- **沉浸对话**：以角色口吻回复，RAG 检索原文保证设定一致性
- **角色标记 RAG**：向量索引标注角色，对话时按角色名过滤检索上下文
- **自定义头像**：为角色上传头像图片，本地持久化
- **用户身份设定**：选择自己在故事中扮演的角色身份
- **多格式导入**：支持 .txt / .md / .json / .csv / .log，最大 100MB
- **对话自动摘要**：长对话自动折叠旧消息，防止记忆溢出
- **别名合并**：自动识别"汪东城"和"大东"是同一人
- **SillyTavern v2 导出**：角色卡一键导出为兼容 JSON
- **TTS 语音合成**：Edge TTS 引擎，消息气泡一键播放语音
- **音色选择**：晓晓/云希/晓伊/云扬，设置面板可试听切换
- **文件缓存**：TTS 合成结果 MD5 缓存，命中 0.01s（110x 加速）
- **SQLite 持久化**：角色卡、对话历史、文本库全持久化

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

# 启动桌面版（FastAPI + React 前端）
py -3.12 web/server.py
# 浏览器打开 http://localhost:7860

# 或启动 Gradio 版
py -3.12 web/app.py
# 浏览器打开 http://localhost:7860
```

## 项目结构

```
Character-distill/
├── adapters/
│   └── llm_adapter.py          # DeepSeek API 封装（OpenAI 兼容格式）
├── core/
│   ├── schema.py               # CharacterCard Pydantic 模型
│   ├── distiller.py            # 角色蒸馏引擎（含角色感知段落抽取）
│   ├── rag.py                  # 原文向量检索（ChromaDB + 角色标记过滤）
│   ├── chat_engine.py          # 对话引擎（system prompt + RAG + history）
│   ├── export.py               # SillyTavern v2 JSON 导出
│   └── text_manager.py         # 文本/会话生命周期管理
├── speech/
│   ├── tts_engine.py           # TTS 抽象接口
│   └── edge_tts_client.py      # Edge TTS 客户端（MD5 文件缓存）
├── storage/
│   ├── base.py                 # 存储抽象层
│   ├── sqlite_store.py         # SQLite 实现（角色卡/对话/文本）
│   └── migrations/             # 数据库迁移
├── web/
│   ├── server.py               # FastAPI 入口 + 静态文件托管
│   ├── app.py                  # Gradio 界面（备用）
│   ├── deps.py                 # FastAPI 依赖注入（单例管理）
│   ├── routers/
│   │   ├── chat.py             # 对话 API
│   │   ├── distill.py          # 蒸馏 + 导出 API
│   │   ├── history.py          # 历史管理 API
│   │   ├── text.py             # 文本上传 API
│   │   └── tts.py              # TTS 合成 API
│   ├── frontend/
│   │   ├── src/
│   │   │   ├── components/     # React 组件
│   │   │   │   ├── Sidebar.jsx
│   │   │   │   ├── CharCard.jsx
│   │   │   │   ├── ChatArea.jsx
│   │   │   │   ├── TextPanel.jsx
│   │   │   │   ├── SettingsPanel.jsx
│   │   │   │   ├── HistoryPanel.jsx
│   │   │   │   └── common/     # Avatar, Loading, ErrorBox
│   │   │   ├── store/          # Zustand 全局状态
│   │   │   ├── api/            # HTTP 客户端
│   │   │   └── utils/          # 主题等工具
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
│   └── avatars/                # 角色头像
├── config.yaml                 # 模型/RAG/蒸馏/存储配置
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
    │
    ├─→ core/chat_engine.py     ← 角色卡 system prompt + 角色过滤 RAG + history
    │       │
    │       ▼
    │   LLM → 角色回复
    │
    ├─→ core/export.py          ← CharacterCard → SillyTavern v2 JSON
    │
    └─→ speech/edge_tts_client.py ← 文本 → MD5 缓存 → Edge TTS → audio/mpeg
```

## API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/text/upload` | POST | 上传文本文件 |
| `/api/text/list` | GET | 文本列表 |
| `/api/distill/identify` | POST | 识别文本中角色 |
| `/api/distill` | POST | 蒸馏角色卡 |
| `/api/distill/cards` | GET | 角色卡列表 |
| `/api/distill/cards/{id}/export` | GET | 导出 SillyTavern JSON |
| `/api/distill/reindex/{text_id}` | POST | 重建 RAG 索引 |
| `/api/chat` | POST | 发送消息（SSE 流式） |
| `/api/chat/legacy` | POST | 发送消息（JSON） |
| `/api/chat/reset` | POST | 重置对话 |
| `/api/chat/revoke` | POST | 撤回消息 |
| `/api/history/sessions` | GET | 会话历史列表 |
| `/api/history/sessions/{id}` | DELETE | 删除会话 |
| `/api/tts/synthesize` | POST | TTS 语音合成 |
| `/api/settings/config` | GET | 后端配置信息 |
| `/api/upload/avatar` | POST | 上传角色头像 |

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

## TTS 语音

- **引擎**：Microsoft Edge TTS（`edge-tts` 库）
- **音色**：晓晓（女活泼）、云希（男青年）、晓伊（女温柔）、云扬（男新闻）
- **缓存**：相同文本+音色 MD5 缓存到 `data/tts_cache/`，命中 ~0.01s
- **播放**：角色消息气泡悬停显示播放按钮，全局单例播放

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM | DeepSeek V4 Pro（OpenAI 兼容 API） |
| 向量检索 | sentence-transformers + ChromaDB |
| 后端 | FastAPI + Uvicorn |
| 前端 | React 18 + Vite + Zustand |
| 持久化 | SQLite（aiosqlite） |
| 数据校验 | Pydantic V2 |
| TTS | Microsoft Edge TTS |
| 备用界面 | Gradio |

## 路线图

- [x] React Vite 前端（替换 CDN/Babel）
- [x] FastAPI 路由拆分 + 依赖注入
- [x] SQLite 持久化
- [x] 角色别名合并
- [x] 自定义角色头像
- [x] 用户身份设定
- [x] 多格式文本导入
- [x] 对话自动摘要
- [x] 角色感知段落抽取
- [x] 角色标记 RAG + 过滤查询
- [x] SillyTavern v2 角色卡导出
- [x] TTS 语音合成 + 缓存
- [x] 聊天消息播放按钮 + 音色设置
- [ ] 局势分析（好感度 / 成功概率实时计算）
- [ ] 多角色同时蒸馏 + 群聊模式
- [ ] 微信接入

## License

MIT
