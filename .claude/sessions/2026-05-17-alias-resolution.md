# 2026-05-17 别名合并：角色名统一直到聊天层

### 操作摘要

- **做了什么**：确认名字链路完整性，修复 `ChatEngine` 和集成层使 RAG 角色过滤生效
- **为什么**：`_tag_characters` 和 `query` 已支持别名→主名匹配和角色过滤，但 `ChatEngine` 从未传 `character_name`，且 RAG 索引时未传 `all_characters`，导致角色过滤功能闲置
- **影响范围**：`core/chat_engine.py`（`__init__` 加可选参数，`chat`/`chat_stream` 条件传 `character_name`）、`core/text_manager.py`（`_create_session`/`get_or_distill` 传递角色列表）、`web/app.py`（`run_distill` 传递角色列表）

### 名字链路（完整）

```
identify_characters → [{name:"魏无羡", aliases:["魏婴","夷陵老祖"]}]
    ↓
distill → CharacterCard(name="魏无羡")     ← 系统 prompt 用 card.name
    ↓
_tag_characters → chunk tagged ["魏无羡"]   ← 别名命中 → 记主名
    ↓
query(character_name="魏无羡") → $contains  ← 按主名过滤
    ↓
ChatEngine.chat() → pass card.name           ← 本次修复
```

### 关键决策
- `ChatEngine` 向后兼容：`all_characters` 为 None 时不传 `character_name`，行为不变
- `get_or_distill` 从已有 cards 提取角色名列表（不含 aliases），避免额外 LLM 调用
- `web/app.py` 在自动识别路径下传递完整 `chars`（含 aliases），手动名路径暂不传
