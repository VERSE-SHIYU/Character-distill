# -*- coding: utf-8 -*-
"""卡片叶子文本的**唯一**递归遍历：内容审核与注入守卫共用（锁 S10）。

两条防线（`auto_review` 的内容审核、`card_guard` 的注入守卫）原先各写一份递归，形态相近
却各自演化 —— 卡里多一层嵌套（①迁移后的 `phases[].overlay`）时改一处漏一处，漏掉的那种
字段就没人看。这里只做一件事：把卡字典摊成 `(路径, 文本)` 叶子序列。

路径用点号 + `[i]` 寻址，形如 `character_arc.phases[0].overlay.key_memories[0]` ——
`card_guard.neutralize` 按同一套寻址回写，两处必须同源。非字符串叶子（数字、布尔、null）
不产出。
"""
from __future__ import annotations

from typing import Any, Iterator


def iter_texts(node: Any, prefix: str = "") -> Iterator[tuple[str, str]]:
    """摊平卡片（或任意嵌套 dict/list），产出 `(路径, 文本)`；只产出字符串叶子。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from iter_texts(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from iter_texts(item, f"{prefix}[{i}]")
    elif isinstance(node, str):
        yield prefix, node
