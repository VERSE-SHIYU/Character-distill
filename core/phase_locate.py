# -*- coding: utf-8 -*-
"""阶段起点重定位：模型写的锚点核对不通过时，由代码去原文里找每个阶段从哪一章开始。

spec：`docs/specs/phase-locate.md`。本模块**只做计算**（检索、拼提示词、解析、挑选、取原句），
不调模型、不落卡：判断由调用方注入（`judge`，见 `Distiller.locate_phase_starts`）。

为什么要它：蒸馏时模型读完整本书凭记忆写阶段锚点，记得「发生了什么」、记不准「发生在哪」，常把
回忆里的句子当锚点（祈灼 10-18：阶段 1 锚点在第 383 章章内回忆），整卡位置核对因此跳过。

做法（检索 → 逐章判断 → 取最早，依据见 spec §2）：
1. 每个阶段（2..n）用阶段描述（label + state）在回忆区外的各章里做 BM25（字二元组）检索，取前
   `TOP_K` 章为候选；
2. 每个候选章单独判一次：`key_moments` 第一件是否由此人本人做出或亲身经历、并在**本章当下**
   发生（回忆 / 计划 / 之后的日常都答否），是则给句号；
3. 按章号升序取第一个「是」、且晚于上一阶段起点的章为起点；锚点 = 该句原文（逐字），若这句在
   书里更早处也出现，向后拼接下一句直到全书首次出现就在本章（锚点按首次出现定位）。

任一阶段找不到起点（含 `key_moments` 为空的旧草稿）→ 不给结果（调用方保持原锚点，行为同今天：
整卡跳过位置核对）。
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Sequence

from core.chapters import chapter_starts
from core.llm_json import extract_json
from core.quotes import locate_anchor, normalize
from core.retro_regions import RetroRegions

# 每个阶段的候选章数。离线核过（10-18，锁定真值 5/5 覆盖）：前 10 章只覆盖 4/5（谢凛羽阶段 2 的
# 正确章排第 23），前 25 章 5/5。**全仓只此一处定义**。
TOP_K = 25
# 锚点句向后拼接的上限：拼到这么多句仍不能让首次出现落在本章，就放弃这一阶段（极少见）。
MAX_JOIN = 3

_SENT = re.compile(r"[^。！？!?\n]*[。！？!?…”」]*")
_FILLER = "…—-·.。 　"
_PUNCT = re.compile(r"[，。！？、“”‘’：；…—（）《》「」\W\d]")

JUDGE_SYSTEM = "你是文学分析助手。下面是一部小说的某一章，句子已按顺序编号。请只根据这一章的文字作答。"

# 「阶段从哪件事开始」的唯一定义：维度 L 的 key_moments 规则与判断提示词都引用这一句。
KEY_MOMENT_START = "标志阶段开始的事 = key_moments 第一件"


# ── 章节与句子 ────────────────────────────────────────────────────────────────────

def chapter_bodies(source_text: str) -> dict[int, tuple[int, str]]:
    """``{章号: (正文在原文里的起点, 正文)}``，正文不含标题行；没有可用的章节标题时为空。"""
    ch = chapter_starts(source_text)
    if ch is None:
        return {}
    cuts = [r for _, r in ch] + [len(source_text)]
    out: dict[int, tuple[int, str]] = {}
    for (n, a), b in zip(ch, cuts[1:]):
        nl = source_text.find("\n", a, b)
        start = b if nl == -1 else nl + 1
        out[n] = (start, source_text[start:b])
    return out


def sentences(body: str) -> list[str]:
    """按句末标点与换行切句，去掉只有省略号 / 破折号的行。"""
    return [s.strip() for s in _SENT.findall(body) if s.strip() and s.strip().strip(_FILLER)]


# ── 检索 ──────────────────────────────────────────────────────────────────────────

def _bigrams(t: str) -> list[str]:
    t = re.sub(r"\s", "", t)
    return [t[i:i + 2] for i in range(len(t) - 1) if not _PUNCT.search(t[i:i + 2])]


class ChapterIndex:
    """各章的 BM25（字二元组，k1=1.2、b=0.75 —— Robertson & Zaragoza 2009 的常用取值）。"""

    def __init__(self, bodies: dict[int, str], k1: float = 1.2, b: float = 0.75):
        self._tf = {n: Counter(_bigrams(t)) for n, t in bodies.items()}
        self._dl = {n: sum(c.values()) for n, c in self._tf.items()}
        self._avg = (sum(self._dl.values()) / len(self._dl)) if self._dl else 0.0
        self._df: Counter = Counter()
        for c in self._tf.values():
            self._df.update(set(c))
        self._k1, self._b = k1, b

    def rank(self, query: str) -> list[int]:
        """章号按与 `query` 的相关度降序；同分按章号升序。"""
        terms = set(_bigrams(query))
        n_docs = len(self._tf)
        score: dict[int, float] = {}
        for n, c in self._tf.items():
            s = 0.0
            for w in terms:
                f = c.get(w)
                if f:
                    idf = math.log(1 + (n_docs - self._df[w] + .5) / (self._df[w] + .5))
                    norm = self._k1 * (1 - self._b + self._b * self._dl[n] / (self._avg or 1))
                    s += idf * f * (self._k1 + 1) / (f + norm)
            score[n] = s
        return sorted(score, key=lambda n: (-score[n], n))


# ── 判断提示词与解析 ──────────────────────────────────────────────────────────────

def phase_query(phase: dict) -> str:
    return f"{phase.get('label', '')}：{phase.get('state', '')}"


def _first_key_moment(phase: dict) -> str:
    moments = phase.get("key_moments") or []
    return str(moments[0]).strip() if moments else ""


def judge_prompt(name: str, phase: dict, chapter: int, sents: Sequence[str]) -> str:
    numbered = "\n".join(f"S{i}：{s}" for i, s in enumerate(sents, 1))
    return (f"### 第{chapter}章（已编号）\n{numbered}\n\n"
            f"「{name}」角色弧线的一个阶段：{phase_query(phase)}\n\n"
            f"{KEY_MOMENT_START}：{_first_key_moment(phase)}\n\n"
            f"请判断：这件事是否由「{name}」本人做出或亲身经历，并在**本章正文里当下发生**？\n"
            "- 本章只是回忆、追述、提起这件事（例如「曾」「那时」「当年」），答否；\n"
            "- 本章只是在计划、商量、还没成，答否；\n"
            "- 本章只是这一阶段里的后续日常、而那件事已在前文发生，答否；\n"
            "- 是，则给出这件事发生的那一句的编号。\n"
            "只输出 JSON：{\"happens\": true 或 false, \"sent\": \"S12\" 或 \"\", \"why\": \"一句理由\"}")


@dataclass(frozen=True)
class Verdict:
    happens: bool
    sent: int | None = None
    why: str = ""


def parse_verdict(reply: str, n_sents: int) -> Verdict:
    """回复 → 判断。不是 JSON、句号越界或缺句号，一律按「否」（宁可找不到，不可找错）。"""
    try:
        d = json.loads(extract_json(reply or ""))
    except (ValueError, TypeError):
        return Verdict(False, why="回复不是 JSON")
    if not isinstance(d, dict):
        return Verdict(False, why="回复不是对象")
    m = re.fullmatch(r"S?(\d+)", str(d.get("sent", "")).strip())
    sent = int(m.group(1)) if m and 1 <= int(m.group(1)) <= n_sents else None
    return Verdict(d.get("happens") is True and sent is not None, sent, str(d.get("why", "")))


# ── 编排 ──────────────────────────────────────────────────────────────────────────

@dataclass
class PhaseResult:
    phase: int
    chapter: int | None
    anchor: str
    yes: list[int]
    why: str = ""
    # 每个候选章的判断：（章、是否、why、所选句子；否时句子为空串）—— 供监测行
    judged: list[tuple[int, bool, str, str]] = field(default_factory=list)


@dataclass
class LocateResult:
    """`anchors` 与阶段等长（阶段 1 为空串）；任一阶段没找到时为 None。`rows` 供监测行。"""
    anchors: list[str] | None
    rows: list[PhaseResult] = field(default_factory=list)
    reason: str = ""


Judge = Callable[[list[str]], list[str]]
"""一批判断（每条一个 user 提示词，system 固定 `JUDGE_SYSTEM`）→ 等长的回复列表；失败的那条给空串。"""


def _unique_anchor(source_text: str, source_norm: str, chapter_start: int, chapter_end: int,
                   sents: list[str], i: int) -> str:
    """从第 i 句（0 起）起逐句向后拼，直到全书首次出现落在本章；拼不出返回空串。"""
    lo = len(normalize(source_text[:chapter_start]))
    hi = lo + len(normalize(source_text[chapter_start:chapter_end]))
    for j in range(i, min(i + MAX_JOIN, len(sents))):
        anchor = "".join(sents[i:j + 1])
        loc = locate_anchor(source_text, source_norm, anchor)
        if loc and lo <= loc[0] < hi:
            return anchor
    return ""


def locate(phases: Sequence[dict], source_text: str, retro: RetroRegions, name: str,
           judge: Judge) -> LocateResult:
    """为阶段 2..n 找起点。`phases` 是草稿里的阶段（含 label / state）。"""
    if len(phases) < 2:
        return LocateResult(None, reason="阶段不足 2 个")
    bodies = chapter_bodies(source_text)
    if not bodies:
        return LocateResult(None, reason="原文没有可用的章节标题")
    retro_ch = retro.chapters(source_text)       # 与位置核对（`RetroRegions.spans`）同一份口径
    index = ChapterIndex({n: t for n, (_, t) in bodies.items() if n not in retro_ch})
    sents = {n: sentences(t) for n, (_, t) in bodies.items()}

    jobs: list[tuple[int, int]] = []
    for k in range(2, len(phases) + 1):
        if not _first_key_moment(phases[k - 1]):
            continue                      # 没有标志事件（旧草稿 / 空列表）→ 该阶段找不到起点
        # 检索只用 state（与 10-18 离线覆盖测试、探针 E2 同口径）；判断提示词带 label + state 作背景。
        cands = sorted(index.rank(str(phases[k - 1].get("state", "")))[:TOP_K])
        jobs += [(k, n) for n in cands if sents[n]]
    replies = judge([judge_prompt(name, phases[k - 1], n, sents[n]) for k, n in jobs])
    verdicts = {job: parse_verdict(r, len(sents[job[1]])) for job, r in zip(jobs, replies)}
    judged: dict[int, list[tuple[int, bool, str, str]]] = {}
    for k, n in jobs:
        v = verdicts[(k, n)]
        sent = sents[n][v.sent - 1] if v.happens and v.sent else ""
        judged.setdefault(k, []).append((n, v.happens, v.why, sent))

    source_norm = normalize(source_text)
    ends = sorted(start for start, _ in bodies.values()) + [len(source_text)]
    rows: list[PhaseResult] = []
    anchors, prev = [""], 0
    for k in range(2, len(phases) + 1):
        yes = sorted(n for (kk, n), v in verdicts.items() if kk == k and v.happens)
        row = PhaseResult(k, None, "", yes, judged=judged.get(k, []))
        for n in (c for c in yes if c > prev):
            start = bodies[n][0]
            end = next(e for e in ends if e > start)
            v = verdicts[(k, n)]
            anchor = _unique_anchor(source_text, source_norm, start, end, sents[n], v.sent - 1)
            if anchor:
                row = PhaseResult(k, n, anchor, yes, v.why, judged.get(k, []))
                break
        rows.append(row)
        if row.chapter is None:
            return LocateResult(None, rows, f"阶段 {k} 找不到起点")
        anchors.append(row.anchor)
        prev = row.chapter
    return LocateResult(anchors, rows)
