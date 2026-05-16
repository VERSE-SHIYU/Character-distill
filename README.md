# 📖 角色模拟器 Character Simulator

> 上传任何文本（小说、聊天记录、人物描写），自动蒸馏出角色人格，跟他沉浸式对话。

## 功能

- 🧬 **角色蒸馏**：从文本自动提取性格、说话风格、价值观、内在矛盾、人际关系
- 💬 **沉浸对话**：以角色口吻回复，RAG 检索原文保证设定一致性
- 🖼️ **自定义头像**：为角色上传头像图片，本地持久化
- 👤 **用户身份设定**：选择自己在故事中扮演的角色身份
- 📁 **多格式导入**：支持 .txt / .md / .json / .csv / .log，最大 100MB
- 💾 **会话持久化**：角色卡和对话历史自动保存 localStorage，刷新不丢
- 📋 **对话自动摘要**：长对话自动折叠旧消息，防止记忆溢出
- 🔗 **别名合并**：自动识别"汪东城"和"大东"是同一人
- 🔌 **可扩展**：适配器模式支持切换 LLM，角色卡结构兼容 SillyTavern 标准

## 快速开始

```bash
git clone <repo>
cd Character-distill

# 安装依赖（Python 3.12+）
py -3.12 -m pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

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
│   └── llm_adapter.py       # DeepSeek API 封装（OpenAI 兼容格式）
├── core/
│   ├── schema.py            # CharacterCard Pydantic 模型
│   ├── distiller.py         # 角色蒸馏引擎
│   ├── rag.py               # 原文向量检索（ChromaDB 内存模式）
│   └── chat_engine.py       # 对话引擎（system prompt + history）
├── web/
│   ├── server.py            # FastAPI 后端 + 静态文件托管
│   ├── app.py               # Gradio 界面（备用）
│   └── static/
│       ├── index.html        # 入口页
│       ├── desktop-app.jsx   # 桌面端 React 组件
│       ├── desktop-style.css # 桌面端样式
│       ├── ios-frame.jsx     # 移动端 iOS 模拟器外壳
│       ├── novel-sim-v3.jsx  # 移动端主组件
│       └── tweaks-panel.jsx  # 移动端调参面板
├── tests/
│   ├── test_chat.py
│   ├── test_rag.py
│   ├── test_distill.py
│   └── test_connection.py
├── config.yaml              # 模型/RAG/蒸馏配置
├── requirements.txt
├── .env.example
└── README.md
```

## 架构流程

```
用户输入文本
    │
    ▼
adapters/llm_adapter.py      ← DeepSeek API（OpenAI 兼容）
    │
    ├─→ core/distiller.py    ← 角色蒸馏（输出 CharacterCard JSON）
    │       │
    │       ▼
    │   core/schema.py       ← Pydantic 结构化校验
    │
    ├─→ core/rag.py          ← 原文向量检索（sentence-transformers + ChromaDB）
    │
    └─→ core/chat_engine.py  ← 对话引擎
            │
            ▼
        角色卡 system prompt + RAG 上下文 + 多轮 history → LLM → 角色回复
```

## API 接口

| 端点 | 方法 | 请求体 | 响应 |
|------|------|--------|------|
| `/api/identify` | POST | `{ text }` | `{ characters: [{name, importance, reason}] }` |
| `/api/distill` | POST | `{ text, character_name? }` | CharacterCard + session_id |
| `/api/chat` | POST | `{ session_id, message }` | `{ reply, rag_context }` |
| `/api/reset` | POST | `{ session_id }` | `{ ok: true }` |

## 配置说明

`config.yaml` 关键配置：

```yaml
llm:
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"
  temperature: 0.7
  max_tokens: 4096

rag:
  chunk_size: 500        # 文本分块大小
  chunk_overlap: 50      # 分块重叠
  top_k: 3              # 检索返回数量
  embedding_model: "all-MiniLM-L6-v2"

distill:
  max_input_chars: 80000  # 蒸馏输入上限（约 8 万字）
```

## 蒸馏方法论

蒸馏 prompt 设计参考：
- **Nuwa-skill**：认知框架分层提取（身份→性格→价值观→矛盾）
- **BookWorld**（ACL 2025）：小说角色一致性建模

核心原则：
1. **跨场景验证**：一个特质必须在 ≥2 个不同场景出现才能写入
2. **有预测力**：提取的特质能预测此人在新情境下的反应
3. **保留矛盾**：矛盾是真实人格的标志，不准美化、不准调和
4. **忠于原文**：他是什么样就是什么样，不添加、不美化、不删减

## 对话机制

每次用户发送消息：
1. **RAG 检索**：用消息在原文向量库中找最相关片段
2. **构建 prompt**：角色卡全维度 + RAG 原文 + 多轮历史 → system prompt
3. **LLM 生成**：DeepSeek 以角色身份回复
4. **行为约束**：永不承认是 AI、保持口癖、表现矛盾、不编造

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM | DeepSeek V4 Pro（OpenAI 兼容 API） |
| 向量检索 | sentence-transformers + ChromaDB |
| 后端 | FastAPI + Uvicorn |
| 前端（桌面） | React 18 + Babel Standalone（纯 CDN） |
| 前端（移动） | React 18 iOS 模拟器风格 |
| 备用界面 | Gradio |
| 数据校验 | Pydantic V2 |

## 路线图

- [x] React 桌面前端替换 Gradio
- [x] 会话 localStorage 持久化
- [x] 角色别名合并
- [x] 自定义角色头像
- [x] 用户身份设定
- [x] 多格式文本导入
- [x] 对话自动摘要
- [ ] 局势分析（好感度 / 成功概率实时计算）
- [ ] 多角色同时蒸馏 + 群聊模式
- [ ] SillyTavern 角色卡导出（`.json`）
- [ ] 后端 session 持久化（SQLite/Redis）
- [ ] 微信接入

## License

MIT
