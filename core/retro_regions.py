# -*- coding: utf-8 -*-
"""「回忆区」—— 书里**倒叙**的章节：这一章写的事，发生在它前面的正文已经写到的时间之前。

为什么要有：阶段锚点与证据摘录都按「在原文里的位置」归阶段，默认文本位置 = 故事时间；
倒叙章节打破这个前提（谢凛羽草稿：锚点与 9 条证据都落在第 491–504 章的日札里）。模型自己
分不清当下与回忆（arXiv 2610.21468 §5.2、2610.17402 §5），故先让它**按章**标出倒叙章节，
再由代码统一使用（2610.17402 §6：先建时间线、再标注）。

口径是**时间顺序**，不是体裁：时间接在正文之后的番外（谢凛羽原文第 505–507 章，结局之后的
日常）位置 = 时间仍成立，不标；讲过去的番外（第 508–509 章，云钺童年）要标。第一版按体裁问
「日记 / 番外」，两次实测都把第 505 章标了进去（PR #179 审计），故改为按时间问。

本模块**不认识 `Distiller`**：调用模型的函数由调用方注入（`call(system, user) -> 回复文本`），
整书前缀也由调用方给 —— 与 `core/relationship_batch.py` 同一形状。回忆区是**书的属性**，
按书缓存见 `core/retro_cache.py`。

**章号由代码定，不由模型数**：模型对每个范围只给首章、末章各一句原文，代码逐字定位
（`core.quotes.locate_anchor`，与阶段锚点同一个入口）再换算成章号。按时间口径的提示词实测
（PR #179 探针 run1）仍把第 505 章并进第 503–504 章的日札 —— 80 万 token 的上下文里数章号、
找边界不可靠；这与阶段锚点「模型给原文、代码核位置」是同一个分工（oberst-lab/
verifiable-by-construction，arXiv 2609.15964）。引文对不上或不唯一，整个结果判「未标」、
不缓存：丢掉一个范围就是漏标。

漏标与多标都有害：漏标让倒叙内容被当成当下；多标让该章里的正确锚点被当成失败。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from core.chapters import chapter_at, chapter_norm_starts, chapter_starts
from core.llm_json import extract_json
from core.quotes import locate_anchor, normalize, paragraph_bounds

logger = logging.getLogger(__name__)

# 识别口径的版本：提示词或解析规则一改就 +1，库里的旧结果随之失效（同 `Distiller.IDENTIFY_VERSION`）。
RETRO_VERSION = 1

RETRO_INSTRUCTION = (
    "## 本步：标出倒叙的章节\n"
    "通读上面的全文。大多数章节按故事时间往下写：后面章节写的事，发生在前面章节写的事之后。"
    "请列出**不是**这样的章节范围 —— 这一章整章（或几乎整章）写的事，发生在它前面的正文已经写到的"
    "时间之前。常见的有：角色写的日记、日札、书信，回头记从前的经历；讲过去的番外（童年、前传）；"
    "整章都在回忆过去。\n"
    "不算：时间接在前面正文之后的章节，即使标着番外（例如结局之后的日常）；正常往下写、"
    "只夹了几句回忆的章节。\n"
    "相邻、同类的章节合成一个范围。每个范围给两句原文定位，章号由程序按原文位置算，不用你写：\n"
    "- start_quote：范围第一章开头附近的一句；\n"
    "- end_quote：范围最后一章结尾附近的一句。\n"
    "两句都逐字照抄原文，不改字、不加省略号，各挑一句在全书里只出现一次的完整句子。"
    "再写明类别（日记 / 书信 / 番外 / 回忆），理由写清这些章写的是什么时候的事、为什么早于前面的正文。\n"
    "只输出 JSON，不要别的文字：\n"
    '{"ranges": [{"kind": "类别", "start_quote": "原文", "end_quote": "原文", "why": "理由"}]}\n'
    "没有这类章节就输出 {\"ranges\": []}。"
)
RETRO_USER = "请列出倒叙的章节范围，只输出 JSON。"


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


class _Unlocatable(ValueError):
    """一句定位引文在原文里找不到、或不止一处。"""


def _chapter_of(quote: object, *, source_text: str, source_norm: str,
                paragraphs: list[int] | None, chapters: list[tuple[int, int]],
                norm_starts: list[int]) -> int:
    if not isinstance(quote, str) or not quote.strip():
        raise _Unlocatable(f"引文为空：{quote!r}")
    locs = locate_anchor(source_text, source_norm, quote, paragraphs)
    if not locs or len(locs) != 1:
        raise _Unlocatable(f"引文{'找不到' if not locs else f'出现 {len(locs)} 处'}：{quote[:30]}")
    chapter = chapter_at(chapters, norm_starts, locs[0])
    if chapter is None:
        raise _Unlocatable(f"引文在第一章之前：{quote[:30]}")
    return chapter


def parse(reply: str, source_text: str, chapters: list[tuple[int, int]]) -> RetroRegions:
    """模型回复 → 回忆区。每个范围的首末章由两句引文在原文里的位置定；任一范围引文对不上、
    不唯一、或首尾颠倒 → 整个结果「未标」且不缓存（丢掉一个范围就是漏标）。回复不是要的
    JSON 同样不缓存。"""
    try:
        data = json.loads(extract_json(reply))
        rows = data["ranges"]
        if not isinstance(rows, list):
            raise TypeError("ranges 不是数组")
    except (ValueError, KeyError, TypeError) as exc:
        return RetroRegions.unavailable(f"回复格式不对：{exc}", final=False)
    source_norm = normalize(source_text)
    norm_starts = chapter_norm_starts(source_text, chapters, source_norm)
    if norm_starts is None:
        return RetroRegions.unavailable("章节起点换不到规范化坐标", final=True)
    where = dict(source_text=source_text, source_norm=source_norm,
                 paragraphs=paragraph_bounds(source_text, source_norm),
                 chapters=chapters, norm_starts=norm_starts)
    kept: list[ChapterRange] = []
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise _Unlocatable(f"范围格式不对：{row!r}")
            first = _chapter_of(row.get("start_quote"), **where)
            last = _chapter_of(row.get("end_quote"), **where)
            if first > last:
                raise _Unlocatable(f"首章 {first} 在末章 {last} 之后")
        except _Unlocatable as exc:
            logger.warning("[retro] 范围定位失败，整本判未标：%s", exc)
            return RetroRegions.unavailable(f"范围定位失败：{exc}", final=False)
        kept.append(ChapterRange(first, last, str(row.get("kind", ""))))
    return RetroRegions("marked", tuple(sorted(kept, key=lambda r: r.first)))


def detect(source_text: str, *, prefix: str,
           call: Callable[[str, str], str]) -> RetroRegions:
    """标一本书的回忆区：没有可用的章节标题就不问模型，直接「未标」。"""
    chapters = chapter_starts(source_text)
    if chapters is None:
        return RetroRegions.unavailable("原文没有可用的「第N章」标题", final=True)
    return parse(call(f"{prefix}\n\n{RETRO_INSTRUCTION}", RETRO_USER), source_text, chapters)
