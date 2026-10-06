# -*- coding: utf-8 -*-
"""出卡 —— 存储行 → 前端卡片负载的**唯一一处**（B4）。

`character_arc.selectable`（前端据此决定是否渲染阶段选择框，DA8）是**算出来的**：起点齐全 +
有正文指纹（`CharacterArc.has_positions`）。它不是卡里的固有内容，所以**不随卡落库** —— 存库
那一刻算一遍写进 `card_json` 就会过期（旧卡没这个键；位置是后台作业补的，补完那张卡存下的
仍是 false），前端读到的便是陈值，该能选阶段的卡不显示选择框。

故模型里不再把它当计算字段自动序列化；改在**出卡这一处**按当前 phases / 指纹现算。新增接口
只要走 `out_card`，不必各自记得补这个键。

导入方向（§4.0）：本模块只可导入 `core.schema`。
"""
from __future__ import annotations

import json
from typing import Any

from core.schema import CharacterArc


def out_card(row: dict[str, Any]) -> dict[str, Any]:
    """把一行存储记录转成前端卡片负载：`card_json` 里的 `character_arc.selectable` 现算。

    **只补这一个派生键**，其余字段原样透传（卡的内容是存下来的，不在此重算）。`card_json`
    是字符串（存库时 `model_dump_json`）或已是 dict（调用方解好的）都收。
    """
    raw = row.get("card_json")
    if raw is None:
        return row
    card = json.loads(raw) if isinstance(raw, str) else dict(raw)
    arc = card.get("character_arc")
    if isinstance(arc, dict):
        # 只这一处按登记的口径判定，不再各处各判一次。
        card["character_arc"] = {**arc, "selectable": CharacterArc.model_validate(arc).has_positions()}
    if isinstance(raw, str):
        return {**row, "card_json": json.dumps(card, ensure_ascii=False)}
    return {**row, "card_json": card}
