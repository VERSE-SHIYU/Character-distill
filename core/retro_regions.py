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

**章号由代码列、模型只选**：代码按「第N章」标题列出全书目录（每章章号 + 开头 40 字），
放在提问里，模型从目录里选章号。实测（PR #179 探针，6 次）模型认得出**哪些内容**是倒叙
（每次都点对了各角色的日札与云钺番外），但让它自己在 80 万 token 里数章号、或挑首末句定位，
边界都会错一章（第 505 章被并进日札；末句取到前一章结尾，漏第 502、504 章）。章号与内容
就近对照后，「数位置」这件事交还给代码 —— 同本仓 WP17「代码列编号候选、模型只回编号」
（`core.quotes.extract_candidates`）。任一范围章号不在目录里或首尾颠倒，整本判「未标」、
不缓存：丢一个范围就是漏标。

漏标与多标都有害：漏标让倒叙内容被当成当下；多标让该章里的正确锚点被当成失败。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from core.chapters import chapter_openings, chapter_starts
from core.llm_json import extract_json
from core.quotes import normalized_starts

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
    "章号以下面的目录为准（程序按正文的「第N章」标题列出，每章附开头几十个字），"
    "范围的首章、末章都要对照目录确认。\n"
    "每个范围写明类别（日记 / 书信 / 番外 / 回忆），理由写清这些章写的是什么时候的事、"
    "为什么早于前面的正文。\n"
    "只输出 JSON，不要别的文字：\n"
    '{"ranges": [{"from": 起始章号, "to": 结束章号, "kind": "类别", "why": "理由"}]}\n'
    "没有这类章节就输出 {\"ranges\": []}。\n\n"
    "### 目录\n"
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

    def spans(self, source_text: str) -> list[tuple[int, int, str]]:
        """回忆区在**规范化**原文里的半开区间 ``[(起, 止, 标签), …]`` —— 位置核对用的形态。

        区间从首章标题起、到末章的下一章标题止（末章是最后一章则到全文末尾）。未标、或章节
        标题与标注时对不上（原文变了）→ 空表，位置核对行为同没有回忆区。
        """
        if self.status != "marked" or not self.ranges:
            return []
        chapters = chapter_starts(source_text)
        index = {n: i for i, (n, _) in enumerate(chapters or [])}
        if chapters is None or any(r.first not in index or r.last not in index for r in self.ranges):
            logger.warning("[retro] 原文章节与回忆区对不上，本次按未标处理：%s", self.summary())
            return []
        raw = [(chapters[index[r.first]][1],
                chapters[index[r.last] + 1][1] if index[r.last] + 1 < len(chapters)
                else len(source_text),
                f"第{r.first}–{r.last}章{r.kind}") for r in self.ranges]
        points = sorted({x for a, b, _ in raw for x in (a, b)})
        norm = dict(zip(points, normalized_starts(source_text, points)))
        return [(norm[a], norm[b], label) for a, b, label in raw]

    def summary(self) -> str:
        if self.status != "marked":
            return f"未标（{self.reason}）"
        return "、".join(f"第{r.first}–{r.last}章{r.kind}" for r in self.ranges) or "无"


# 不识别回忆区的入口（`Distiller.distill` / `distill_incremental` 这类没有书级缓存的库入口、
# 测试）显式传它：位置核对行为与没有回忆区时完全相同。生产的两个蒸馏入口传 PR-3 算出的结果。
NOT_DETECTED = RetroRegions.unavailable("本入口不识别回忆区", final=False)


def instruction(source_text: str, chapters: list[tuple[int, int]]) -> str:
    """本步指令 + 代码列的全书目录（每章一行：「第N章：开头几十个字」）。"""
    return RETRO_INSTRUCTION + "\n".join(f"第{n}章：{head}"
                                          for n, head in chapter_openings(source_text, chapters))


def parse(reply: str, chapters: list[tuple[int, int]]) -> RetroRegions:
    """模型回复 → 回忆区。任一范围章号不在目录里、或首尾颠倒 → 整本「未标」且不缓存
    （丢掉一个范围就是漏标）；回复不是要的 JSON 同样不缓存。"""
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
            first, last = int(row["from"]), int(row["to"])
        except (KeyError, TypeError, ValueError):
            first = last = None
        if first is None or first > last or first not in known or last not in known:
            logger.warning("[retro] 范围对不上目录，整本判未标：%r", row)
            return RetroRegions.unavailable(f"范围对不上目录：{row!r}"[:200], final=False)
        kept.append(ChapterRange(first, last, str(row.get("kind", ""))))
    return RetroRegions("marked", tuple(sorted(kept, key=lambda r: r.first)))


def detect(source_text: str, *, prefix: str,
           call: Callable[[str, str], str]) -> RetroRegions:
    """标一本书的回忆区：没有可用的章节标题就不问模型，直接「未标」。"""
    chapters = chapter_starts(source_text)
    if chapters is None:
        return RetroRegions.unavailable("原文没有可用的「第N章」标题", final=True)
    return parse(call(f"{prefix}\n\n{instruction(source_text, chapters)}", RETRO_USER), chapters)
