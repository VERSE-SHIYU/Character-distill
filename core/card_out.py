# -*- coding: utf-8 -*-
"""出卡 —— 存储行 → 前端卡片负载的**唯一一处**（B4）。

`character_arc.selectable`（前端据此决定是否渲染阶段选择框，DA8）是**算出来的**：起点齐全 +
有正文指纹（与 `CharacterArc.has_positions` 同一判据，经 `ArcPositions` 只读这两样）。它不是卡里的固有内容，所以**不随卡落库** —— 存库
那一刻算一遍写进 `card_json` 就会过期（旧卡没这个键；位置是后台作业补的，补完那张卡存下的
仍是 false），前端读到的便是陈值，该能选阶段的卡不显示选择框。

故模型里不再把它当计算字段自动序列化；改在**出卡这一处**按当前 phases / 指纹现算。新增接口
只要走 `out_card`，不必各自记得补这个键。

**`revision`（乐观锁，spec arc-phase-unlocated §13）同理是算出来的**：存储行里 `card_json`
原文的内容摘要（`card_revision`）。卡的内容一变它就变，任何写入方都不用记得「顺手加一」；
编辑保存与挪动带上它，路由按同一函数核对、存储层按原文比较后写入。

导入方向（§4.0）：本模块只可导入 `core.schema` 与叶子模块 `core.fingerprint`。
"""
from __future__ import annotations

import json
from typing import Any

from core.fingerprint import content_fingerprint
from core.schema import ArcPositions


# 编辑保存、挪动、市场编辑在版本不符时共用的上屏文案（409）。
CARD_CONFLICT = "这张卡已在别处更新，请刷新后再改"


def card_revision(raw: str | dict) -> str:
    """一张卡当前内容的版本标识：存储行里 `card_json` 原文的内容指纹（`content_fingerprint`）。

    **只此一处定义**：出卡（`out_card`）与写入前的核对（编辑保存、挪动）都调它。传 dict（调用方
    已解析）时按存库同一口径（`ensure_ascii=False`）序列化再算。
    """
    return content_fingerprint(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))


def out_card(row: dict[str, Any]) -> dict[str, Any]:
    """把一行存储记录转成前端卡片负载：`card_json` 里的 `character_arc.selectable` 现算。

    **只补两个派生键**：卡内的 `selectable` 与行上的 `revision`（`card_revision`，按存储
    原文算，不按补过 `selectable` 的串算），其余字段原样透传（卡的内容是存下来的，不在此重算）。`card_json`
    是字符串（存库时 `model_dump_json`）或已是 dict（调用方解好的）都收。
    """
    raw = row.get("card_json")
    if raw is None:
        return row
    row = {**row, "revision": card_revision(raw)}
    card = json.loads(raw) if isinstance(raw, str) else dict(raw)
    arc = card.get("character_arc")
    if isinstance(arc, dict):
        # 只这一处判定；只解析判据要读的起点与指纹（`ArcPositions`），别的字段不合法也不影响出卡（R3）。
        card["character_arc"] = {**arc, "selectable": ArcPositions.model_validate(arc).has_positions()}
    if isinstance(raw, str):
        return {**row, "card_json": json.dumps(card, ensure_ascii=False)}
    return {**row, "card_json": card}
