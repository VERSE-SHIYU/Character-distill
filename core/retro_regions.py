# -*- coding: utf-8 -*-
"""「回忆区」—— 书里不按故事当下顺序推进主线的章节（日记 / 书信 / 番外 / 整章回忆）。

为什么要有：阶段锚点与证据摘录都按「在原文里的位置」归阶段，默认文本位置 = 故事时间；
回忆区打破这个前提（谢凛羽草稿：锚点与 9 条证据都落在第 491–504 章的日札里）。模型自己
分不清当下与回忆（arXiv 2610.21468 §5.2、2610.17402 §5），故先让它**按章**标出回忆区，
再由代码统一使用（2610.17402 §6：先建时间线、再标注）。

本模块**不认识 `Distiller`**：调用模型的函数由调用方注入（`call(system, user) -> 回复文本`），
整书前缀也由调用方给 —— 与 `core/relationship_batch.py` 同一形状。回忆区是**书的属性**，
按书缓存见 `core/retro_cache.py`。

提示词即 2026-10-17 小重放实测的那一版（谢凛羽原文，2 次：日札与番外漏标 0、多标结尾
1–3 章）。多标的代价只是该章摘录不作位置证据，漏标才有害，故只要求不漏。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from core.chapters import chapter_starts
from core.llm_json import extract_json

logger = logging.getLogger(__name__)

# 识别口径的版本：提示词或解析规则一改就 +1，库里的旧结果随之失效（同 `Distiller.IDENTIFY_VERSION`）。
RETRO_VERSION = 1

RETRO_INSTRUCTION = (
    "## 本步：标出不是「当下主线叙述」的章节\n"
    "通读上面的全文，列出所有**不是**按故事当下顺序推进主线的章节范围，包括：角色写的日记、日札、"
    "书信；番外；整章都在回忆或复述过去的章节。正常推进主线、只是夹了几句回忆的章节不算。\n"
    "章节号用正文里「第N章」的 N。每个范围写明类别（日记 / 书信 / 番外 / 回忆）和一句理由。\n"
    "只输出 JSON，不要别的文字：\n"
    '{"ranges": [{"from": 起始章号, "to": 结束章号, "kind": "类别", "why": "理由"}]}\n'
    "没有这类章节就输出 {\"ranges\": []}。"
)
RETRO_USER = "请列出不是当下主线叙述的章节范围，只输出 JSON。"


@dataclass(frozen=True)
class ChapterRange:
    first: int
    last: int
    kind: str


@dataclass(frozen=True)
class RetroRegions:
    """一本书的回忆区。``marked`` = 已标（``ranges`` 可以为空：模型认为没有）；
    ``unavailable`` = 标不了（``reason`` 写明为什么），下游按「未标」处理、行为同今天。

    ``final``：这个结果能不能缓存。没有章节标题、分组路径这类**由原文决定**的结论能缓存；
    模型回复格式不对、调用失败这类**偶发**结果不缓存，下次再试。
    """
    status: str
    ranges: tuple[ChapterRange, ...] = ()
    reason: str = ""
    final: bool = True

    @classmethod
    def unavailable(cls, reason: str, *, final: bool) -> "RetroRegions":
        return cls("unavailable", (), reason, final)

    def to_json(self) -> str:
        return json.dumps({"status": self.status, "reason": self.reason,
                           "ranges": [[r.first, r.last, r.kind] for r in self.ranges]},
                          ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "RetroRegions":
        d = json.loads(text)
        return cls(d["status"], tuple(ChapterRange(a, b, k) for a, b, k in d["ranges"]),
                   d.get("reason", ""), True)

    def summary(self) -> str:
        if self.status != "marked":
            return f"未标（{self.reason}）"
        return "、".join(f"第{r.first}–{r.last}章{r.kind}" for r in self.ranges) or "无"


def parse(reply: str, chapters: list[tuple[int, int]]) -> RetroRegions:
    """模型回复 → 回忆区。章号不在原文里、或首尾颠倒的范围丢掉并 warning；回复不是要的
    JSON → 偶发失败（不缓存）。"""
    try:
        data = json.loads(extract_json(reply))
        rows = data["ranges"]
        if not isinstance(rows, list):
            raise TypeError("ranges 不是数组")
    except (ValueError, KeyError, TypeError) as exc:
        return RetroRegions.unavailable(f"回复格式不对：{exc}", final=False)
    known = {n for n, _ in chapters}
    kept: list[ChapterRange] = []
    for row in rows:
        try:
            first, last, kind = int(row["from"]), int(row["to"]), str(row.get("kind", ""))
        except (KeyError, TypeError, ValueError):
            logger.warning("[retro] 丢掉格式不对的范围：%r", row)
            continue
        if first > last or first not in known or last not in known:
            logger.warning("[retro] 丢掉章号对不上原文的范围：第%s–%s章", first, last)
            continue
        kept.append(ChapterRange(first, last, kind))
    return RetroRegions("marked", tuple(sorted(kept, key=lambda r: r.first)))


def detect(source_text: str, *, prefix: str,
           call: Callable[[str, str], str]) -> RetroRegions:
    """标一本书的回忆区：没有可用的章节标题就不问模型，直接「未标」。"""
    chapters = chapter_starts(source_text)
    if chapters is None:
        return RetroRegions.unavailable("原文没有可用的「第N章」标题", final=True)
    return parse(call(f"{prefix}\n\n{RETRO_INSTRUCTION}", RETRO_USER), chapters)
