### 17:00 角色扮演 prompt 优化 — 角色一致性与沉浸感提升

- **做了什么**：三文件递进改造：schema 加 dialogue_examples 字段 → distiller 蒸馏 prompt 加对话示例提取 → chat_engine system prompt 全面重写（新增 few-shot 示例段 + 回复格式引导 + 铁律从5条扩充到7条）
- **为什么**：用户反馈角色"出戏"——使用网络用语、emoji、meta 评论、不会用动作描写。Few-shot 示例是最有效的防出戏手段（让模型"见过"角色怎么说话再开始回复）
- **影响范围**：
  - `core/schema.py` — CharacterCard 加 `dialogue_examples: list[str] = []`
  - `core/distiller.py` — DISTILL_PROMPT_AFTER_NAME 加第 I 条（对话示例提取）
  - `core/chat_engine.py` — build_system_prompt 重写：
    - 新增【对话风格示范】段（few-shot，旧卡 hasattr 兼容）
    - 新增【回复格式】段（引导（）动作描写、50-150字约束、禁止旁白）
    - 【行为规则】→【铁律】从5条扩到7条（禁网络用语、禁 emoji、禁 meta 评论）

### 验证结果

| 检查项 | 状态 |
|--------|------|
| schema 旧卡兼容 | ✅ dialogue_examples=[] |
| schema 新卡 | ✅ 示例写入成功 |
| distiller 含对话示例提取 | ✅ 1 match |
| chat_engine 三段新增 | ✅ 3 |
| dialogue_examples 引用 | ✅ 2 |
| 旧卡 prompt 不含示例段 | ✅ |
| 新卡 prompt 含示例段 | ✅ |
| 铁律7条 | ✅ |
| 前端 build | ✅ 180ms |
