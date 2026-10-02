"""Parse JSON out of LLM replies.

Models wrap JSON in markdown fences, add prose before/after it, or leave
trailing commas. Every place that parses a model's JSON reply goes through
here, so a model swap that changes output formatting is handled once.
"""
from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> str:
    """剥掉 markdown 围栏、前后解释文字、修尾随逗号，提取完整 JSON 对象。

    处理 LLM 常见脏输出：````json` 围栏、JSON 前后的自然语言解释、
    尾随逗号（trailing comma）、以及嵌套大括号场景。
    """
    t = text.strip()

    # 1. 剥 markdown code fence —— 支持 ```json ... ``` 和 ``` ... ```
    if t.startswith("```"):
        # 找到第二个 ``` 作为 fence 结束
        parts = t.split("```")
        # parts[0] = "" (opening fence), parts[1] = maybe "json\n...", parts[2..] = rest
        if len(parts) >= 3:
            # 取第一个 fence 和第二个 fence 之间的内容
            t = parts[1]
            if t.startswith("json"):
                t = t[4:]
            t = t.strip()
        elif len(parts) == 2:
            # 只有开头 fence 没有结尾 (````... 开头但没有闭合)
            t = parts[1].strip()

    # 2. 提取 JSON —— 同时支持对象 {} 和数组 []
    #    取第一个有效的 { 或 [ 作为起点，对应闭合符的最后一个作为终点
    brace_pos = t.find("{")
    bracket_pos = t.find("[")
    first_brace = brace_pos if brace_pos != -1 else float("inf")
    first_bracket = bracket_pos if bracket_pos != -1 else float("inf")

    if first_brace == float("inf") and first_bracket == float("inf"):
        return t  # 没有任何 JSON 结构

    is_array = first_bracket < first_brace
    if is_array:
        open_ch, close_ch = "[", "]"
        start = bracket_pos
        end = t.rfind("]")
    else:
        open_ch, close_ch = "{", "}"
        start = brace_pos
        end = t.rfind("}")

    if end <= start:
        return t

    candidate = t[start:end + 1]

    # 括号配对校验（跳过字符串内容）
    depth = 0
    in_string = False
    escaped = False
    for ch in candidate:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
    if depth == 0:
        t = candidate
    # 括号不成对时保留 candidate（尽力而为）

    # 3. 去尾随逗号：},] 和 ,] ， ]
    t = re.sub(r",\s*([}\]])", r"\1", t)

    return t


def loads_llm_json(text: str) -> Any:
    """``json.loads`` on the JSON extracted from an LLM reply. Raises on failure."""
    return json.loads(extract_json(text))
