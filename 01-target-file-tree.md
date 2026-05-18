# 角色模拟器重构 — 目标文件树

> 本文档是重构的唯一蓝图。所有提示词执行结果必须符合此结构。

## 架构原则

1. 后端按领域分 router，不在 server.py 堆端点
2. 存储层抽象接口，SQLite 是实现之一
3. 前端 Vite + React，构建产物 → `web/frontend/dist/` → FastAPI 托管
4. `adapters/`、`core/schema.py`、`core/distiller.py`、`core/rag.py` 只扩展不改签名
5. 移动端旧文件冻结到 `web/static/_deprecated/`

## 目标结构

```
Character-distill/
│
├── adapters/
│   ├── __init__.py
│   └── llm_adapter.py              # ✅ 不动。DeepSeek API 封装
│
├── core/
│   ├── __init__.py
│   ├── schema.py                    # ✅ 不动。CharacterCard Pydantic 模型
│   ├── distiller.py                 # ✅ 不动。角色蒸馏引擎
│   ├── rag.py                       # ✅ 不动。ChromaDB 向量检索
│   ├── chat_engine.py               # ⚠️ 扩展：加摘要压缩方法 summarize_history()
│   └── text_manager.py              # 🆕 文本管理：解析多格式、分块、缓存蒸馏结果
│
├── storage/
│   ├── __init__.py
│   ├── base.py                      # 🆕 抽象接口 StorageBase（ABC）
│   │                                #     定义：save_text / get_text / list_texts / delete_text
│   │                                #           save_card / get_card / list_cards
│   │                                #           save_session / get_session / list_sessions
│   │                                #           save_message / get_messages / search_messages
│   │                                #           delete_session / export_session
│   ├── sqlite_store.py              # 🆕 SQLite 实现 StorageBase
│   │                                #     数据库文件：data/character_sim.db
│   │                                #     表：texts / cards / sessions / messages
│   └── migrations/                  # 🆕 SQL 迁移脚本目录
│       └── 001_init.sql             #     建表语句
│
├── web/
│   ├── server.py                    # ⚠️ 重构：只保留 app 实例 + 挂载 router + 托管静态
│   ├── app.py                       # ✅ 不动。Gradio 备用界面
│   ├── deps.py                      # 🆕 FastAPI 依赖注入（get_storage, get_llm, get_config）
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── text.py                  # 🆕 /api/text/*   文本上传/列表/删除
│   │   ├── distill.py               # 🆕 /api/distill/* 蒸馏+角色识别（从server.py迁移+增强）
│   │   ├── chat.py                  # 🆕 /api/chat/*   对话（含SSE流式）/重置/撤回
│   │   └── history.py               # 🆕 /api/history/* 历史记录搜索/导出/删除
│   │
│   ├── frontend/                    # 🆕 Vite + React 项目
│   │   ├── package.json
│   │   ├── vite.config.js           #     proxy /api → localhost:7860
│   │   ├── index.html
│   │   ├── src/
│   │   │   ├── main.jsx             #     React 入口
│   │   │   ├── App.jsx              #     主路由 + 全局状态
│   │   │   ├── api/
│   │   │   │   └── client.js        #     fetch 封装 + 超时 + 错误处理 + SSE
│   │   │   ├── store/
│   │   │   │   ├── db.js            #     IndexedDB 封装（头像/大文本缓存）
│   │   │   │   └── useAppStore.js   #     Zustand 全局状态
│   │   │   ├── components/
│   │   │   │   ├── Sidebar.jsx      #     左侧导航（主页/文本/角色/历史/设置）
│   │   │   │   ├── TextPanel.jsx    #     文本上传 + 已导入列表
│   │   │   │   ├── CharCard.jsx     #     角色卡展示 + 头像上传
│   │   │   │   ├── ChatArea.jsx     #     聊天区（流式渲染/撤回/摘要折叠）
│   │   │   │   ├── HistoryPanel.jsx #     历史记录（搜索/筛选/继续/导出）
│   │   │   │   ├── SettingsPanel.jsx#     设置（API配置/主题切换）
│   │   │   │   └── common/
│   │   │   │       ├── Avatar.jsx   #     头像组件（图片/首字母/颜色哈希）
│   │   │   │       ├── Loading.jsx  #     加载状态
│   │   │   │       └── ErrorBox.jsx #     错误提示
│   │   │   └── styles/
│   │   │       └── global.css       #     全局样式（迁移自 desktop-style.css）
│   │   └── dist/                    #     构建产物（git ignore，由 npm run build 生成）
│   │
│   └── static/
│       └── _deprecated/             # ❄️ 冻结归档
│           ├── novel-sim-v3.jsx
│           ├── ios-frame.jsx
│           ├── tweaks-panel.jsx
│           ├── desktop-app.jsx      #     旧桌面版（重写后归档）
│           ├── desktop-style.css
│           ├── distill-cache.js
│           └── index.html
│
├── data/                            # 🆕 运行时数据目录（git ignore）
│   └── character_sim.db             #     SQLite 数据库文件
│
├── tests/
│   ├── test_chat.py                 # ✅ 保留
│   ├── test_rag.py                  # ✅ 保留
│   ├── test_distill.py              # ✅ 保留
│   ├── test_connection.py           # ✅ 保留
│   ├── test_storage.py              # 🆕 存储层测试
│   └── test_api.py                  # 🆕 API 端点测试
│
├── content/                         # ✅ 不动。示例文本
├── config.yaml                      # ⚠️ 扩展：加 storage 段
├── requirements.txt                 # ⚠️ 扩展：加 aiosqlite
├── .env.example                     # ✅ 不动
├── .gitignore                       # ⚠️ 加 data/ web/frontend/dist/ node_modules/
├── LICENSE                          # ✅ 不动
└── README.md                        # ⚠️ 最后更新
```

## 各模块职责速查

| 模块 | 职责 | 依赖 |
|------|------|------|
| `storage/base.py` | 抽象接口，定义所有存储方法签名 | 无 |
| `storage/sqlite_store.py` | SQLite实现，aiosqlite异步 | base.py, aiosqlite |
| `core/text_manager.py` | 文本格式解析(.txt/.md/.json/.csv/.log) + 存储调用 | storage |
| `web/deps.py` | FastAPI DI：单例 storage/llm/config | storage, adapters |
| `web/routers/text.py` | 文本CRUD API | text_manager, deps |
| `web/routers/distill.py` | 蒸馏+识别 API（含全局蒸馏缓存） | distiller, deps |
| `web/routers/chat.py` | 对话API（SSE流式） + 撤回 + 摘要 | chat_engine, deps |
| `web/routers/history.py` | 历史搜索/导出/删除 | storage, deps |
| `web/frontend/` | Vite React SPA | zustand, idb |

## API 设计

### 文本管理
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/text/upload` | POST | 上传文本（multipart file 或 raw text） |
| `/api/text/list` | GET | 已导入文本列表 |
| `/api/text/{id}` | DELETE | 删除文本及关联角色卡 |

### 蒸馏
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/distill/identify` | POST | `{text_id}` → 角色列表 |
| `/api/distill/run` | POST | `{text_id, character_name}` → 角色卡 + session_id |
| `/api/distill/cards/{text_id}` | GET | 该文本已蒸馏的所有角色卡 |

### 对话
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat/send` | POST | `{session_id, message}` → SSE 流式回复 |
| `/api/chat/revoke` | POST | `{session_id, message_index}` → 撤回该消息及之后 |
| `/api/chat/reset` | POST | `{session_id}` → 清空历史 |

### 历史
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/history/list` | GET | `?keyword=&character=&page=` |
| `/api/history/{session_id}` | GET | 获取完整对话 |
| `/api/history/{session_id}` | DELETE | 删除会话 |
| `/api/history/{session_id}/export` | GET | `?format=json|txt` 导出 |

## 数据库 Schema

```sql
-- texts：导入的原文
CREATE TABLE texts (
    id TEXT PRIMARY KEY,           -- UUID
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- cards：蒸馏出的角色卡
CREATE TABLE cards (
    id TEXT PRIMARY KEY,
    text_id TEXT NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    card_json TEXT NOT NULL,        -- CharacterCard.model_dump_json()
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- sessions：对话会话
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    user_role TEXT DEFAULT '',
    avatar_data TEXT DEFAULT '',    -- base64 或空
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- messages：聊天消息
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,             -- 'user' | 'char' | 'summary'
    content TEXT NOT NULL,
    rag_context TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
