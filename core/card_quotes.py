# -*- coding: utf-8 -*-
"""卡片层的引文核对：卡片里凡引号括起来的「原文」，查不到就把引号去掉。

引文（角色自述、他人评价）是卡片里最容易被编造的成分 —— 读着像原文，却查无此句。
WP17 已让「对话示例」由代码从原文复制、逐字由构造保证；这一层把同样的保证扩到卡片其余
字段里的**引文**与**口癖**。

只动 `VERIFIED_FIELDS` 里的字段：清单外（`name`/`tags`/`first_message`/`awakening_message`/
`taboo_words`/各处 `vocabulary_level`/`relationships` 的 `target`/`relation`/`note`/`psyche`
数值）要么是创作口吻、要么引号里本就不是原文，动它们只会破坏卡片。

判定与验收共用 `core.quotes`：产品去掉查不到的引文的引号，验收脚本用同一套 `quoted_spans`
+ `verbatim_in_normalized` 复查 —— 各写一份，产品保证的与验收证明的迟早分家。
"""
from __future__ import annotations

import logging
from typing import Any

from core.quotes import normalize, quoted_spans, verbatim_in_normalized
from core.schema import CharacterCard

logger = logging.getLogger(__name__)

# 要核对的字段路径（`a.b[].c` 形状）。**唯一出处**：核对、验收、测试都读这一份。
# 只列「引号里应是原文」的字段；不在清单里的一律不动。
VERIFIED_FIELDS: tuple[str, ...] = (
    "personality_traits[]",
    "values[]",
    "key_memories[]",
    "inner_tensions[]",
    "emotional_patterns[]",
    "decision_style",
    "psyche.soft_spots[]",
    "cognitive.speech_style",
    "speaking_style.sentence_pattern",
    "relationships[].attitude",
    "situation_behaviors[].behavior",
    "character_arc.phases[].behaviors[].behavior",
)

# 整个值就声明「是原文」的字段（同样的路径形状）。**唯一出处**。对不上没有可保留的部分：
# 列表元素整条删（口癖）；对象里的键清成空串、对象本身留下（摘录没了，条目还在）。
VERBATIM_FIELDS: tuple[str, ...] = (
    "speaking_style.catchphrases[]",
    "situation_behaviors[].source_quote",
    "character_arc.phases[].behaviors[].source_quote",
)


def _descend(node: Any, segs: list[str], label: str, out: list) -> None:
    """按 `a.b[].c` 展开到可读写的槽位，把 `(字段路径, 容器, 键/下标)` 收进 `out`。

    缺失的键跳过：卡来自 `model_dump()` 时形状是齐的，但验收侧可能拿到残缺 dict，
    少一个字段不该让整次核对抛异常。
    """
    seg, *rest = segs
    prefix = f"{label}." if label else ""
    if seg.endswith("[]"):
        key = seg[:-2]
        items = (node or {}).get(key) or []
        for i, item in enumerate(items):
            sub = f"{prefix}{key}[{i}]"
            if rest:
                _descend(item, rest, sub, out)
            else:
                out.append((sub, items, i))
        return
    if rest:
        _descend((node or {}).get(seg) or {}, rest, f"{prefix}{seg}", out)
        return
    if isinstance(node, dict) and seg in node:
        out.append((f"{prefix}{seg}", node, seg))


def _slots(card: dict, paths: tuple[str, ...]) -> list[tuple[str, Any, Any]]:
    out: list[tuple[str, Any, Any]] = []
    for path in paths:
        _descend(card, path.split("."), "", out)
    return out


def verified_slots(card: dict) -> list[tuple[str, Any, Any]]:
    """核对清单里的槽位：`(字段路径, 容器, 键)`，`容器[键]` 可直接读改。

    公开给验收脚本，让产品与验收读**同一份** `VERIFIED_FIELDS`（各写一份必漂）。
    """
    return _slots(card, VERIFIED_FIELDS)


def _retract_unverified_verbatim(dump: dict, source_norm: str, retracted: list[dict]) -> None:
    """`VERBATIM_FIELDS` 里对不上原文的值：列表元素删掉，对象里的键清空。

    倒序处理：删列表元素不会挪动还没处理的下标。空值不算撤回（本来就没有摘录）。
    """
    for label, parent, key in reversed(_slots(dump, VERBATIM_FIELDS)):
        value = parent[key]
        if not value or verbatim_in_normalized(source_norm, value):
            continue
        field = label.rsplit("[", 1)[0] if isinstance(parent, list) else label
        retracted.append({"field": field, "quote": value})
        if isinstance(parent, list):
            del parent[key]
        else:
            parent[key] = ""


def _strip_unverified_quotes(text: str, source_norm: str, label: str,
                             retracted: list[dict]) -> str:
    """把 `text` 里查不到的引文的**引号字符**去掉，保留文字；每次撤回记进 `retracted`。

    收集要删的引号字符位置再一次性重建：嵌套引语（外层被撤、内层也有一条）时按位置删不会
    被偏移打乱。
    """
    drop: set[int] = set()
    for start, end, inner in quoted_spans(text):
        if verbatim_in_normalized(source_norm, inner):
            continue
        drop.add(start)
        drop.add(end - 1)
        retracted.append({"field": label, "quote": inner})
    if not drop:
        return text
    return "".join(ch for i, ch in enumerate(text) if i not in drop)


def retract_unverified(card: CharacterCard, content: str) -> tuple[CharacterCard, list[dict]]:
    """清单内查不到的引文去掉引号，口癖对不上的整条删除；返回 `(新卡, 撤回清单)`。

    引文去引号而非整条删：查不到的多在列表项里（如一条关键记忆），整条删会连同正确的事件
    一起删掉；去引号后这段文字不再冒充原文，「引号里的必是原文」这条保证仍成立，留下的文字
    若有错由人工编辑改正。口癖本身声明「是原话」，对不上没有可保留的部分，故整条删（也不
    设字数下限，同验收口径）。

    `content` 先归一化一次，随后几十条引文都拿它比 —— `verbatim_in` 每条都会重新归一化整本
    原文（80 万字 × 几十条）。卡不就地改。每条撤回记一行 `logger.warning`。
    """
    dump = card.model_dump()
    source_norm = normalize(content)
    retracted: list[dict] = []

    for label, parent, key in verified_slots(dump):
        text = parent[key]
        if isinstance(text, str):
            new = _strip_unverified_quotes(text, source_norm, label, retracted)
            if new != text:
                parent[key] = new

    _retract_unverified_verbatim(dump, source_norm, retracted)

    for r in retracted:
        logger.warning("[card_quotes] 撤回查不到的引文 %s：%s", r["field"], r["quote"])
    return CharacterCard.model_validate(dump), retracted
