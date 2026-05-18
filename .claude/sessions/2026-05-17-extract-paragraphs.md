# 2026-05-17 角色感知段落抽取

### HH:MM 操作摘要

- **做了什么**：在 `core/distiller.py` 的 `Distiller` 类中新增 `_extract_character_paragraphs` 方法，并修改 `distill` 方法使用角色感知段落抽取替代简单截断
- **为什么**：原文 `text[:max_input_chars]` 只取前 N 字，若角色在中后段出场则蒸馏输入几乎不含角色信息。新方法按角色名/别名定位段落并取前后各 2 段上下文，确保蒸馏时有足够的角色相关信息
- **影响范围**：`core/distiller.py`（新增 1 个私有方法，修改 `distill` 的文本准备逻辑），外部接口不变

### 关键决策
- 段落切分优先按 `\n\n`，段落过少时回退为每 500 字切块
- 匹配窗口：命中段落 ±2 段
- 两次安全回退：匹配为 0 时回退到全文截断；聚焦文本 < 500 字时回退
- `distill` 额外调用 `identify_characters` 以获取 aliases，增加了 1 次 LLM 调用
