# -*- coding: utf-8 -*-
"""卡片关系条目的结构去重：同一 `target` 只留第一条。

刘姥姥验收实测：一张卡里「贾母」出现两条（`relation` 分别是「老亲家、贾府老祖宗」与
「老亲家」）—— 同一个人被拆成两条，注入时同一段关系被说两遍。§10 C4 的关系判据就是
「不许有重复 target」，这一层让产品自己保证它，而不是留给验收去发现。

**不合并文字。** 两条的 relation/attitude/note 措辞不同，谁去谁留没有依据，合并只会编出
第三条既非此也非彼的表述。留**第一条**是可复算的规则（顺序由模型给的原文决定）。

**为什么不在 `core/card_quotes.py`。** 那一层只管「引号里的必是原文」，其 docstring 明写
`relationships` 的 `target`/`relation`/`note` 不在核对清单里 —— 两件事不同：那边改文字，
这边整条丢。故另开一层，与它并列挂在 `Distiller.finalize_card` 上。
"""
from __future__ import annotations

import logging

from core.schema import CharacterCard

logger = logging.getLogger(__name__)


def dedupe_relationship_targets(card: CharacterCard) -> tuple[CharacterCard, list[dict]]:
    """关系条目按 `target` 去重，只留第一条；返回 `(新卡, 丢弃清单)`。

    空 `target` 不算「同一个 target」：没名字的条目谈不上是同一个人，一律保留（与验收
    `duplicate_targets` 同口径）。每条丢弃记一行 `logger.warning`，字段、target、被丢的
    relation 都报出来 —— 落卡后卡上只剩一条，从成品看不出曾有过第二条（`docker logs` 是
    唯一痕迹）。
    """
    dump = card.model_dump()
    seen: set[str] = set()
    kept: list[dict] = []
    dropped: list[dict] = []
    for rel in dump.get("relationships") or []:
        target = str((rel or {}).get("target") or "")
        if target:
            if target in seen:
                dropped.append({"field": "relationships", "target": target,
                                "relation": str((rel or {}).get("relation") or "")})
                continue
            seen.add(target)
        kept.append(rel)
    if not dropped:
        return card, dropped
    for d in dropped:
        logger.warning("[card_relationships] 丢弃重复 target 的关系条目 %s：target=%s，"
                       "被丢的 relation=%s", d["field"], d["target"], d["relation"])
    dump["relationships"] = kept
    return CharacterCard.model_validate(dump), dropped
