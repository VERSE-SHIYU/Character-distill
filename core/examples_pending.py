# -*- coding: utf-8 -*-
"""「待补对话示例」—— 这件事的规则只在本模块（docs/specs/examples-pending.md）。

一张卡蒸馏出来没配上对话示例，就给它记「待补」：`cards.examples_pending_for`，行上的一列，值是
蒸馏时找示例用的角色名（空 = 不待补）。它不在卡的内容里 —— 卡的内容会被原样复制到复制卡、
发布的版本、导出文件，流程状态不该跟着走。角色页据此在打开这张卡时弹出编辑页，让用户自己
填，或让系统重新找一次。

连名字一起记，是因为重新找要用蒸馏时的那个名字：卡上的 `name` 是模型在格式化那一步写的，
代码不保证它和蒸馏时指定的角色名一致。

三个出口都清掉它 —— 用户保存了卡、让系统重新找过、关掉了提醒 —— 所以提醒只有蒸馏好的
这一次，「重新找」也只有一次。

- `mark_after_distill`：落卡后记（每条产卡通道落卡后都调它）；
- `settle`：三个出口共用的「清掉」；
- `refind`：重新找一次，用的是蒸馏里贴示例的同一步（`Distiller.attach_dialogue_examples`）。

导入方向：只导入 `core.*`；存储与蒸馏器经参数传入（同 `core.character_roster`）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.character_roster import aliases_for, resolve_characters
from core.distiller import DistillError, Distiller
from core.nonfatal import nonfatal
from core.schema import CharacterCard

logger = logging.getLogger(__name__)

# 行上那一列的名字：读（存储层的行）和判断「是不是待补」都用它。
PENDING_FOR = "examples_pending_for"


async def mark_after_distill(
    storage: Any, card_id: str, user_id: str, card: CharacterCard, character: str,
) -> None:
    """落卡后按这一次蒸馏的结果记「待补」：没有示例就记下 `character`（蒸馏时找示例用的角色名），
    有示例就清掉。

    每次落卡都重记：重新蒸馏同一个角色（写回同一行）配上了示例，上一次留下的「待补」随之清掉。
    有没有示例只由 `CharacterCard.has_dialogue_examples` 判。

    记不上不让落卡失败 —— 卡已经存下，少的只是一次提醒；失败经 `nonfatal` 上报。
    """
    async with nonfatal("examples_pending", "mark card after distill"):
        await storage.set_card_examples_pending(
            card_id, user_id, None if card.has_dialogue_examples() else character)


async def settle(storage: Any, card_id: str, user_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """清掉「待补」，返回清过的行。保存、重新找、关掉三个出口共用。"""
    if row.get(PENDING_FOR):
        await storage.set_card_examples_pending(card_id, user_id, None)
    return {**row, PENDING_FOR: None}


async def refind(
    storage: Any, distiller: Distiller, record: dict[str, Any], user_id: str,
) -> CharacterCard | None:
    """给 `record` 这张待补的卡重新找一次对话示例：找到了返回贴好示例的卡，找不到返回 None。

    用的名字是蒸馏时那一个（记在 `examples_pending_for` 里），别名与名单里的其他人从名单取 ——
    与蒸馏时贴示例那一步的入参相同。

    找不到 = 原文已经不在 / 原文里没有这个角色的对话句 / 名单里没有别人 / 模型没选出可用的
    （后三种是 `attach_dialogue_examples` 抛的 `DistillError`）。调用模型出错不是「找不到」，
    照实抛给调用方 —— 那一次机会没有用掉。

    只算出结果，不写库、不清「待补」：写回要带版本核对，由调用方做。
    """
    name = record[PENDING_FOR]
    text_id = record.get("text_id") or ""
    text_rec = await storage.get_text_owned(text_id, user_id) if text_id else None
    if not text_rec:
        return None
    content = text_rec["content"]
    card = CharacterCard.model_validate_json(record["card_json"])
    chars = await resolve_characters(storage, distiller, text_id, user_id, content)
    try:
        return await asyncio.to_thread(
            distiller.attach_dialogue_examples,
            card, content, name, aliases_for(chars, name), chars)
    except DistillError as exc:
        logger.info("[examples_pending] 「%s」重新找对话示例：没有找到（%s）", name, exc)
        return None
