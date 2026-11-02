# -*- coding: utf-8 -*-
"""卡片关系对象先按识别名单归一，再按 `target` 去重：同一人只留第一条。

刘姥姥验收实测：一张卡里「贾母」出现两条（`relation` 分别是「老亲家、贾府老祖宗」与
「老亲家」）—— 同一个人被拆成两条，注入时同一段关系被说两遍。§10 C4 的关系判据就是
「不许有重复 target」，这一层让产品自己保证它，而不是留给验收去发现。

**归一是前提。** 模型给关系对象写的「标准名」是它自己定的，同一个人常留下两种写法
（云绮卡第 19 条「柳若芙」与第 37 条「慕容昭瑜」）。识别名单才是本项目判断「谁是谁」
的依据，故落卡前先按名单把存人名的两个字段换成主名，再逐字去重。同时挂在两个人身上的
称呼不换（判不出归属）；名单里没有的对象原样保留并记 WARNING。

**不合并文字。** 两条的 relation/attitude/note 措辞不同，谁去谁留没有依据，合并只会编出
第三条既非此也非彼的表述。留**第一条**是可复算的规则（顺序由模型给的原文决定）。

**为什么不在 `core/card_quotes.py`。** 那一层只管「引号里的必是原文」，其 docstring 明写
`relationships` 的 `target`/`relation`/`note` 不在核对清单里 —— 两件事不同：那边改文字，
这边整条丢。故另开一层，与它并列挂在 `Distiller.finalize_card` 上。
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from core.schema import CharacterCard

logger = logging.getLogger(__name__)


def _canonical_index(roster: Sequence[dict]) -> dict[str, str]:
    """主名→自己；别名→主人，但只在该别名恰好归一个人、且不是任何人的主名时。"""
    index: dict[str, str] = {}
    for entry in roster:
        name = str((entry or {}).get("name") or "")
        if name:
            index[name] = name
    owners: dict[str, str] = {}
    ambiguous: set[str] = set()
    for entry in roster:
        name = str((entry or {}).get("name") or "")
        if not name:
            continue
        for alias in (entry or {}).get("aliases") or []:
            alias = str(alias)
            if not alias or alias in index:
                ambiguous.add(alias)
                continue
            if owners.setdefault(alias, name) != name:
                ambiguous.add(alias)
    index.update({a: o for a, o in owners.items() if a not in ambiguous})
    return index


def dedupe_relationship_targets(
    card: CharacterCard, roster: Sequence[dict]
) -> tuple[CharacterCard, list[dict]]:
    """关系对象按名单归一后按 `target` 去重，只留第一条；返回 `(新卡, 丢弃清单)`。

    `roster` 必填：给了默认值，漏传名单的调用方会静默退回逐字去重，看不出错。
    关系与 `character_arc.unlocated.attitudes` 用同一套映射换名 —— 未定位态度挪回阶段时
    按逐字 `target` 找关系，两个字段不同步就会报「关系已不存在」。

    空 `target` 不算「同一个 target」：没名字的条目谈不上是同一个人，一律保留（与验收
    `duplicate_targets` 同口径）。每条丢弃记一行 `logger.warning`，字段、归一后的 target、
    原始写法（与归一后的 target 不同时才附 `original_target`）和被丢的 relation 都报出来
    —— 落卡后卡上只剩一条，从成品看不出曾有过第二条（`docker logs` 是唯一痕迹）。
    """
    dump = card.model_dump()
    index = _canonical_index(roster)
    seen: set[str] = set()
    kept: list[dict] = []
    dropped: list[dict] = []
    changed = False
    for rel in dump.get("relationships") or []:
        original = str((rel or {}).get("target") or "")
        target = index.get(original, original)
        if roster and original and original not in index:
            logger.warning("[card_relationships] 关系对象不在识别名单里，原样保留：target=%s",
                           original)
        if target:
            if target in seen:
                entry = {"field": "relationships", "target": target,
                         "relation": str((rel or {}).get("relation") or "")}
                if original != target:
                    entry["original_target"] = original
                dropped.append(entry)
                continue
            seen.add(target)
            if target != original:
                rel["target"] = target
                changed = True
        kept.append(rel)

    unlocated = (dump.get("character_arc") or {}).get("unlocated") or {}
    for attitude in unlocated.get("attitudes") or []:
        original = str((attitude or {}).get("target") or "")
        target = index.get(original, original)
        if target != original:
            attitude["target"] = target
            changed = True

    for d in dropped:
        origin = f"（原写法={d['original_target']}）" if "original_target" in d else ""
        logger.warning("[card_relationships] 丢弃重复 target 的关系条目 %s：target=%s%s，"
                       "被丢的 relation=%s", d["field"], d["target"], origin, d["relation"])
    if not dropped and not changed:
        return card, dropped
    dump["relationships"] = kept
    return CharacterCard.model_validate(dump), dropped
