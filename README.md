# 角色模拟器 Character Simulator

> 上传任何文本（小说、聊天记录、人物描写），自动蒸馏出角色人格，跟他沉浸式对话。

## 功能

- **角色蒸馏**：从文本自动提取性格、说话风格、价值观、内在矛盾、人际关系
- **角色感知段落抽取**：蒸馏时自动定位角色相关段落，聚焦原文证据
- **场景索引**：蒸馏后自动建立场景级向量索引，支持小说章节和聊天记录两种格式切分
- **情感感知 RAG**：检索时按情感距离矩阵加权，情绪基调相近的片段优先命中
- **情感锚点历史**：构建对话上下文时强制保留近期非平静情感消息，防止情绪突变被截断
- **沉浸对话**：以角色口吻回复，RAG 检索原文保证设定一致性
- **角色标记 RAG**：向量索引标注角色，对话时按角色名过滤检索上下文
- **角色撤回**：角色回复后 20% 概率触发撤回判断，前端显示"对方撤回了一条消息"，点击可偷看原文
- **网络搜索增强**：对话时可选开启现实信息补充，上下文引入实时网络内容
- **长期记忆**：Mem0 本地记忆引擎，跨会话保留角色对用户的记忆
- **对话前身份设定**：点击"开始对话"先弹出模态框，设定你在故事中的扮演角色
- **用户账号系统**：JWT 认证 + Refresh Token 轮转，邀请码注册，管理员后台
- **个人资料页**：头像上传（云端持久化）、修改密码，三级头像降级（会话专属 → 账号全局 → 首字母）
- **多格式导入**：支持 .txt / .md / .json / .csv / .log / .pdf / .docx，最大 10MB
- **文本元数据**：上传时填写标题和描述，列表展示
- **对话自动摘要**：长对话自动折叠旧消息，防止记忆溢出
- **别名合并**：自动识别"汪东城"和"大东"是同一人
- **角色卡编辑**：对话中随时修改角色卡字段，实时生效
- **开场白生成**：角色卡创建后自动生成符合性格的第一句话
- **SillyTavern v2 导出**：角色卡一键导出为兼容 JSON
- **TTS 语音合成**：Edge TTS 引擎（晓晓/云希/晓伊/云扬），消息气泡一键播放
- **自定义音色库**：上传人声样本创建私人音色，管理/试听/删除
- **音色克隆**：GPT-SoVITS 参考音频上传，角色语音回复
- **语音输入**：FunASR 语音识别，按住录音发送消息
- **会话垃圾桶**：软删除 + 还原，支持彻底清除
- **会话导出**：JSON / TXT 两种格式导出完整对话记录
- **三套精炼主题**：奶油抹茶（暖杏）、蓝色海盐（蓝绿潮汐）、樱花薰衣草（淡雅粉紫），侧边栏和设置面板一键切换，刷新保持
- **PWA 支持**：manifest + Service Worker，可安装到桌面
- **面包屑导航**：侧边栏实时显示当前位置路径
- **文件缓存**：TTS 合成结果 MD5 缓存，命中 0.01s（110x 加速）
- **SQLite 持久化**：角色卡、对话历史、文本库、音色引用、用户数据全持久化

## 快速开始

### 方式一：Docker 生产部署（推荐）

```bash
git clone <repo>
cd Character-distill

# 配置环境变量
cp .env.example .env
# 编辑 .env，至少填入：
#   JWT_SECRET=$(openssl rand -hex 32)
#   DEEPSEEK_API_KEY=your_key
#   ALLOWED_ORIGINS=https://yourdomain.cn

# 启动（OpenResty + FastAPI + Fail2Ban + GoAccess）
docker compose -f docker-compose.prod.yml up -d
```

详细部署流程见 [DEPLOY.md](DEPLOY.md)。

### 方式二：本地开发启动

```bash
git clone <repo>
cd Character-distill

# 安装依赖（Python 3.12+）
pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env，填入 JWT_SECRET 和 DEEPSEEK_API_KEY

# 构建前端
cd web/frontend
npm install
npm run build
cd ../..

# 启动服务
python -m uvicorn web.server:app --host 0.0.0.0 --port 7860
# 浏览器打开 http://localhost:7860

# 前端开发模式（热更新）
cd web/frontend && npm run dev
```

### 方式三：Windows 一键启动（本地 + GPT-SoVITS）

```batch
# 编辑 start_all.bat，确认 GPT-SoVITS 路径与你实际安装位置一致
# 双击 start_all.bat 即可启动全部服务
# 双击 stop_all.bat 清理所有进程
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
│   ├── rag.py                  # 原文向量检索（ChromaDB + 情感加权 + 角色标记过滤）
│   ├── scene_indexer.py        # 场景级向量索引（小说/聊天记录双格式）
│   ├── context_engine.py       # 上下文构建（情感锚点历史 + 网络搜索增强）
│   ├── chat_engine.py          # 对话引擎（system prompt + RAG + 长期记忆 + 撤回判断）
│   ├── memory_manager.py       # Mem0 长期记忆管理
│   ├── export.py               # SillyTavern v2 JSON 导出
│   └── text_manager.py         # 文本/会话生命周期管理
├── speech/
│   ├── tts_engine.py           # TTS 抽象接口
│   ├── edge_tts_client.py      # Edge TTS 客户端（MD5 文件缓存）
│   ├── voice_clone.py          # GPT-SoVITS 音色克隆客户端
│   ├── funasr_client.py        # FunASR WebSocket 客户端
│   └── asr_client.py           # ASR 抽象接口
├── storage/
│   ├── base.py                 # 存储抽象层
│   ├── sqlite_store.py         # SQLite 实现
│   └── migrations/             # 数据库迁移（001~022）
├── web/
│   ├── server.py               # FastAPI 入口（JWT 中间件 + WAF + 限流）
│   ├── deps.py                 # 依赖注入（单例 + 用户 LLM 缓存）
│   ├── security.py             # 安全响应头中间件
│   ├── limiter.py              # slowapi 限流配置
│   ├── routers/
│   │   ├── auth.py             # 认证 API（注册/登录/刷新/头像/密码）
│   │   ├── admin.py            # 管理员 API（用户管理/邀请码/用量统计）
│   │   ├── chat.py             # 对话 API（含流式 SSE + 撤回 + 好感度）
│   │   ├── distill.py          # 蒸馏 API（后台任务 + 流式 + 场景索引）
│   │   ├── history.py          # 历史管理 API（垃圾桶/恢复/导出）
│   │   ├── text.py             # 文本上传 API
│   │   ├── card.py             # 角色卡头像 API
│   │   ├── voice.py            # 音色库 + 音色克隆 + ASR API
│   │   ├── wechat.py           # 微信接入 API（占位）
│   │   └── wechat_utils.py     # 微信加解密工具
│   └── frontend/
│       └── src/
│           ├── components/
│           │   ├── Sidebar.jsx         # 侧边栏（导航+面包屑）
│           │   ├── HomePage.jsx        # 主页
│           │   ├── LoginPage.jsx       # 登录/注册页
│           │   ├── ProfilePage.jsx     # 个人资料（头像+密码）
│           │   ├── TextPanel.jsx       # 文本管理
│           │   ├── CharCard.jsx        # 角色管理（蒸馏+详情+编辑）
│           │   ├── ChatArea.jsx        # 聊天（TTS+录音+撤回+头像）
│           │   ├── HistoryPanel.jsx    # 历史会话（垃圾桶+恢复）
│           │   ├── SettingsPanel.jsx   # 设置（主题+音色+克隆）
│           │   ├── VoicePanel.jsx      # 音色管理
│           │   ├── AdminPanel.jsx      # 管理员后台
│           │   ├── EditCardModal.jsx   # 角色卡编辑模态框
│           │   ├── DistillTaskBar.jsx  # 蒸馏进度条
│           │   ├── ThemeSwitcher.jsx   # 三主题切换
│           │   ├── RoleSetupModal.jsx  # 对话前身份设定
│           │   └── common/             # Avatar, Loading, ErrorBox
│           ├── store/
│           │   └── useAppStore.js      # Zustand 全局状态
│           ├── api/
│           │   └── client.js           # HTTP/SSE 客户端（含 JWT 自动刷新）
│           └── utils/
│               └── theme.js            # 主题切换
├── nginx/
│   ├── nginx.conf              # OpenResty 生产配置（HTTPS + WAF + CC限流）
│   ├── waf.lua                 # WAF 规则（SQL注入/XSS/路径遍历/恶意UA）
│   └── cc.lua                  # CC 防护（10s 滑动窗口 + 自动封禁）
├── fail2ban/
│   ├── jail.local              # Fail2Ban 监狱配置
│   └── filter.d/               # 自定义过滤规则
├── scripts/
│   ├── backup.sh               # SQLite 每日备份脚本（含 COS 上传）
│   ├── test_distill.py         # 蒸馏功能测试脚本
│   └── migrate_data.py         # 数据迁移工具
├── tests/
│   ├── test_chat.py
│   ├── test_rag.py
│   ├── test_connection.py
│   └── test_integration.py
├── data/                       # 运行时数据（自动创建）
│   ├── character_sim.db        # SQLite 数据库
│   ├── voice_cache/            # TTS/克隆缓存
│   ├── voice_library/          # 自定义音色库
│   └── ref_audio/              # 参考音频
├── docker-compose.yml          # 开发环境编排
├── docker-compose.prod.yml     # 生产环境编排
├── Dockerfile                  # 多阶段构建（node → python）
├── start_all.bat               # Windows 一键启动
├── stop_all.bat                # Windows 一键停止
├── config.example.yaml         # 配置模板（复制为 config.yaml 使用）
├── requirements.txt            # 生产依赖
├── requirements-dev.txt        # 开发额外依赖（Gradio）
├── .env.example
├── DEPLOY.md                   # 生产部署详细指南
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
    │       ├─→ core/schema.py  ← Pydantic 结构化校验
    │       │
    │       └─→ core/scene_indexer.py  ← 场景级向量索引（蒸馏后自动建立）
    │
    ├─→ core/rag.py             ← 分块 + 角色标记 + 情感加权 → ChromaDB 向量索引
    │       │
    │       └─→ core/embeddings.py  ← 安全 SentenceTransformer 工厂（CPU 锁定）
    │
    ├─→ core/context_engine.py  ← 情感锚点历史 + 网络搜索增强 → 上下文构建
    │
    ├─→ core/chat_engine.py     ← 角色卡 system prompt + RAG + 长期记忆 + 撤回判断
    │       │
    │       ├─→ core/memory_manager.py  ← Mem0 跨会话记忆
    │       │
    │       └─→ LLM → 角色回复
    │
    ├─→ core/export.py          ← CharacterCard → SillyTavern v2 JSON
    │
    ├─→ speech/edge_tts_client.py  ← Edge TTS 语音合成 + MD5 缓存
    │
    ├─→ speech/voice_clone.py   ← GPT-SoVITS 音色克隆（角色语音回复）
    │
    └─→ speech/funasr_client.py ← FunASR 语音识别（语音输入）
```

## 安全架构

```
外网请求
    │
    ▼
腾讯云 CDN（DDoS + 隐藏源站 IP）
    │
    ▼
OpenResty（WAF + CC 限流 + HTTPS 终结 + 安全响应头）
    │
    ▼
FastAPI（JWT 中间件 + CORS + slowapi 限流 + 全局异常处理）
    │
    ├─→ AuthMiddleware        ← Bearer Token 验证，所有 /api/* 请求强制认证
    ├─→ Depends(get_current_user)  ← 路由层第二道锁，防中间件被绕过
    ├─→ 资源所有权校验        ← 每个资源操作均验证 user_id 归属
    └─→ SQLite               ← 数据完全隔离，用户只能访问自己的数据

Fail2Ban 监听 OpenResty 日志，自动封禁高频攻击 IP
```

## Meta Tensor 防御

项目使用三层防御策略解决 PyTorch `meta` device 问题（accelerate 在 CPU 环境将模型路由到 meta 设备导致的 "Cannot copy out of meta tensor" 错误）：

1. **Layer 1** — `core/fix_meta_tensor.py`：进程级 env vars + `torch.set_default_device("cpu")` + `nn.Module.to` monkey-patch，在 `server.py` 最早的 import 处执行
2. **Layer 2** — `distiller.py` / `text_manager.py` 方法级守卫：在每个可能触发模型加载的方法入口重新设置 env vars 和 torch 默认设备
3. **Layer 3** — `embeddings.py` 调用点防御：`SentenceTransformer` 加载前强化 env vars，加载后检测 meta device 并 `to_empty` 回退

## API 接口

### 认证（`/api/auth`）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/auth/register` | POST | 注册（需邀请码） |
| `/api/auth/login` | POST | 登录，返回 access_token + refresh_token |
| `/api/auth/refresh` | POST | 刷新 Token（Refresh Token 轮转） |
| `/api/auth/logout` | POST | 登出，吊销所有 Refresh Token |
| `/api/auth/me` | GET | 当前用户信息 + API 配置状态 |
| `/api/auth/api-config` | PATCH | 更新个人 API Key / base_url / model |
| `/api/auth/usage` | GET | 个人用量统计 |
| `/api/auth/avatar` | PUT / GET | 上传/获取账号头像 |
| `/api/auth/password` | PUT | 修改密码（需验证旧密码） |

### 管理员（`/api/admin`，需 is_admin）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/admin/users` | GET | 用户列表 |
| `/api/admin/users/{id}/disable` | POST | 禁用用户 |
| `/api/admin/users/{id}/enable` | POST | 启用用户 |
| `/api/admin/users/{id}/reset-password` | PATCH | 重置密码 |
| `/api/admin/users/{id}` | DELETE | 删除用户（级联清除所有数据） |
| `/api/admin/invite/generate` | POST | 生成邀请码 |
| `/api/admin/invite/list` | GET | 邀请码列表 |
| `/api/admin/invite/{code}` | DELETE | 删除邀请码 |
| `/api/admin/invites/used` | DELETE | 批量删除已用邀请码 |
| `/api/admin/usage` | GET | 所有用户用量汇总 |
| `/api/settings/config` | GET / POST | 读写全局 LLM + 语音配置（管理员） |

### 文本（`/api/text`）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/text/upload` | POST | 上传文本（文件或粘贴，含标题/描述/类型元数据） |
| `/api/text/list` | GET | 文本列表 |
| `/api/text/{id}/download-cleaned` | GET | 下载清洗后纯文本 |
| `/api/text/{id}` | DELETE | 删除文本（级联删除角色卡/会话） |

### 蒸馏（`/api/distill`）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/distill/identify` | POST | 识别文本中角色 |
| `/api/distill/run` | POST | 同步蒸馏角色卡 |
| `/api/distill/run_stream` | POST | 流式蒸馏（SSE，实时渲染 Token） |
| `/api/distill/start` | POST | 后台异步蒸馏任务，立即返回 task_id |
| `/api/distill/task/{task_id}` | GET | 轮询蒸馏任务进度 |
| `/api/distill/task/{task_id}` | DELETE | 取消蒸馏任务 |
| `/api/distill/start_session` | POST | 为已有角色卡创建新会话 |
| `/api/distill/card/{card_id}` | PATCH | 编辑角色卡字段 |
| `/api/distill/generate-opening` | POST | 生成角色开场白 |
| `/api/distill/cards/by-text/{text_id}` | GET | 文本下所有角色卡列表 |
| `/api/distill/cards/{card_id}/export` | GET | 导出 SillyTavern v2 JSON |
| `/api/distill/reindex/{text_id}` | POST | 重建 RAG 角色标记索引 |

### 对话（`/api/chat`）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat/send` | POST | 发送消息（SSE 流式 / JSON，含撤回字段） |
| `/api/chat/revoke` | POST | 撤回消息（DB + 内存同步） |
| `/api/chat/affinity/{session_id}` | GET | 获取会话好感度/信任/心情/戒备值 |
| `/api/chat/reset` | POST | 重置对话历史（保留角色卡） |

### 历史（`/api/history`）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/history/list` | GET | 会话列表（分页 + 关键词/角色/文本过滤） |
| `/api/history/trash` | GET | 垃圾桶（软删除的会话） |
| `/api/history/trash/purge` | DELETE | 清空垃圾桶 |
| `/api/history/clear-all` | POST | 全部移入垃圾桶 |
| `/api/history/{id}` | GET | 会话详情 + 完整消息列表 |
| `/api/history/{id}/resume` | POST | 恢复会话（重建 ChatEngine + 注入历史） |
| `/api/history/{id}/restore` | POST | 从垃圾桶还原会话 |
| `/api/history/{id}/export` | GET | 导出会话（JSON / TXT） |
| `/api/history/{id}` | DELETE | 软删除（移入垃圾桶）或彻底删除 |

### 语音（`/api/voice`）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/voice/status` | GET | 语音服务健康状态 |
| `/api/voice/list` | GET | 全部音色（预设 + 自定义） |
| `/api/voice/upload` | POST | 上传自定义音色样本（音频/视频均可） |
| `/api/voice/{voice_id}` | DELETE | 删除自定义音色 |
| `/api/voice/preview-audio/{voice_id}` | GET | 试听音色 |
| `/api/voice/synthesize` | POST | 语音合成（GPT-SoVITS 优先，降级 Edge TTS） |
| `/api/voice/ref-audio/{card_id}` | GET | 获取角色参考音频信息 |
| `/api/voice/ref-audio/upload` | POST | 上传角色参考音频 |
| `/api/voice/ref-audio/{card_id}` | DELETE | 删除角色参考音频 |
| `/api/voice/asr` | POST | 语音转文字（FunASR） |

### 角色卡头像（`/api/cards`）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/cards/{card_id}/avatar` | GET / PUT | 获取/保存角色卡头像（base64） |

## 配置说明

`config.yaml` 关键配置：

```yaml
llm:
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"
  temperature: 0.7
  max_tokens: 4096
  summary_threshold: 100  # 触发对话摘要的消息轮数

rag:
  chunk_size: 500
  chunk_overlap: 50
  top_k: 3
  embedding_model: "all-MiniLM-L6-v2"

distill:
  chunk_size: 5000         # 蒸馏分块大小
  max_profile_len: 8000    # 单角色档案上限

storage:
  type: sqlite
  path: data/character_sim.db

voice:
  enabled: false           # GPT-SoVITS 音色克隆开关
  gptsovits_url: "http://127.0.0.1:9880"
  funasr_url: "ws://127.0.0.1:10095"
  ref_audio_min_seconds: 30
  ref_audio_max_seconds: 60
  default_speed: 1.0

memory:
  enabled: true            # Mem0 长期记忆开关
  provider: "local"
  search_top_k: 10
  context_window: 30
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
1. **RAG 检索**：用消息在原文向量库中找最相关片段（按角色名 + 情感距离加权过滤）
2. **场景检索**：在场景索引中按角色名检索相关场景片段
3. **长期记忆**：Mem0 检索跨会话角色记忆注入上下文
4. **情感锚点历史**：构建历史时强制保留近期非平静情感消息
5. **网络搜索**（可选）：开启后补充实时信息
6. **构建 prompt**：角色卡全维度 + RAG + 场景 + 记忆 + 历史 → system prompt
7. **LLM 生成**：DeepSeek 以角色身份流式回复
8. **撤回判断**：20% 概率触发轻量 LLM 调用，判断角色是否后悔这句话
9. **行为约束**：永不承认是 AI、保持口癖、表现矛盾、不编造

## 语音功能

### Edge TTS（内置）
- **引擎**：Microsoft Edge TTS（`edge-tts` 库）
- **音色**：晓晓（女活泼）、云希（男青年）、晓伊（女温柔）、云扬（男新闻）
- **缓存**：相同文本+音色 MD5 缓存，命中 ~0.01s

### 自定义音色库
- **上传**：wav/mp3/flac/ogg/m4a 音频或 mp4/mov/avi/mkv/webm 视频，自动提取音轨
- **管理**：列表展示（名称、时长、日期），试听原始音频，删除

### 音色克隆（GPT-SoVITS）
- **参考音频**：为角色上传 30-60 秒参考音频 + 文字标注（支持视频自动提取）
- **语音回复**：角色消息使用克隆音色朗读，GPT-SoVITS 不可用时自动降级 Edge TTS
- **语音输入**：按住录音按钮 → FunASR 转文字 → 自动发送

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM | DeepSeek V4 Pro（OpenAI 兼容 API） |
| 向量检索 | sentence-transformers + ChromaDB |
| 长期记忆 | Mem0（本地模式） |
| 后端 | FastAPI + Uvicorn |
| 认证 | JWT（PyJWT）+ Argon2 密码哈希（pwdlib） |
| 反向代理 | OpenResty（Nginx + Lua WAF） |
| 前端 | React 18 + Vite + Zustand |
| 持久化 | SQLite（aiosqlite） |
| 数据校验 | Pydantic V2 |
| TTS | Microsoft Edge TTS + GPT-SoVITS |
| ASR | FunASR（WebSocket） |
| 容器化 | Docker + Docker Compose |
| 入侵防御 | Fail2Ban + OpenResty CC 限流 |
| 设计 | 毛玻璃 glassmorphism，三套主题 |

## 路线图

- [x] React Vite 前端（替换 CDN/Babel）
- [x] FastAPI 路由拆分 + 依赖注入
- [x] SQLite 持久化（22 个迁移版本）
- [x] 用户账号系统（JWT + Refresh Token + 邀请码）
- [x] 管理员后台（用户管理 + 邀请码 + 用量统计）
- [x] 个人资料页（头像云端持久化 + 修改密码）
- [x] 三级头像降级（会话专属 → 账号全局 → 首字母）
- [x] 安全加固（OWASP 双层认证 + IDOR 防护 + WAF）
- [x] Docker 生产部署（OpenResty + Fail2Ban + GoAccess）
- [x] 角色别名合并
- [x] 对话前身份设定模态框
- [x] 多格式文本导入（含 PDF/DOCX）
- [x] 文本元数据（标题+描述）
- [x] 对话自动摘要
- [x] 角色感知段落抽取
- [x] 场景级向量索引（小说/聊天记录双格式）
- [x] 情感感知 RAG（距离矩阵加权 + 锚点历史）
- [x] Mem0 长期记忆（跨会话）
- [x] 角色撤回机制（LLM 判断 + 前端偷看）
- [x] 网络搜索增强对话
- [x] 角色标记 RAG + 过滤查询
- [x] 角色卡实时编辑 + 开场白生成
- [x] SillyTavern v2 角色卡导出
- [x] Edge TTS 语音合成 + 缓存
- [x] 自定义音色库（上传/试听/管理）
- [x] GPT-SoVITS 音色克隆 + 语音回复
- [x] FunASR 语音输入
- [x] 会话垃圾桶 + 导出
- [x] 毛玻璃 UI + 面包屑导航
- [x] 三套精炼主题 + ThemeSwitcher
- [x] PWA 可安装
- [x] Meta tensor 三层防御
- [x] 嵌入模型单例 + ChromaDB 持久化（冷启动 15s → 1-2s）
- [ ] 多角色同时蒸馏 + 群聊模式
- [ ] 微信公众号接入

## License

MIT
