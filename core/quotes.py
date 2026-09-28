# -*- coding: utf-8 -*-
"""原文引语 —— 抽带说话人的对话候选句，核对一段文字是否逐字出自原文。

**纯计算，不 import 任何项目模块**（同 `core/roster_aggregate.py` 的做法）：本模块只认识
「原文是一段字符串、角色名是一串称呼」这个形状，不认识 LLM、不认识提示词、不认识卡片。

为什么单独一个模块：产品侧（按编号从原文挑选对话示例）与验收侧（核对卡片里的对话与
引文是否逐字出自原文）要用**同一套**判定。各写一份，验收判「逐字」的口径迟早和产品
保证的口径分家 —— 那时验收就再也证明不了产品。

判据的中心是「引导语」：原文的对话形态是「凤姐忙和刘姥姥摆手道：“……”」，说话人写在
紧贴引号的引导语里（公开版本实测：86% 的对话句有引导语）。抽取靠它归属说话人，核对
靠它把引号内的台词与出处对上。
"""

from __future__ import annotations

import re
from typing import NamedTuple, Sequence

# 归一化：去空白与标点。卡片里的标点写法与原文不保证一致（全角半角、有无皆可能），
# 不归一的话同一句话会因为一个逗号之差被判查不到。**只此一处** —— 抽取与核对共用。
_DROP_CHARS = frozenset("，。！？、；：“”‘’「」『』（）《》…—" + ",.!?;:\"'()<>-")


def normalize(s) -> str:
    """只留实义字符：去全部空白与标点。"""
    return "".join(ch for ch in str(s) if not ch.isspace() and ch not in _DROP_CHARS)


_ELLIPSIS = re.compile(r"…+|\.{3,}")


def verbatim_in(source: str, quote: str) -> bool:
    """`quote` 是否逐字出现在 `source` 里（归一化后比对，省略处允许跳过）。

    写成「甲……乙」的节选按省略号拆开逐段核对：整串一定查不到（省掉的那些话在原文里
    还在），但两段各自是原文中连着的话 —— 这是合法的引用形态，不拆会把每一处节选都
    判成编造。

    没有任何实义字符的 quote（空串、「……」）返回 False：无可核对的东西不算「找到了」。
    """
    src = normalize(source)
    segs = [s for s in (normalize(p) for p in _ELLIPSIS.split(str(quote))) if s]
    return bool(segs) and all(s in src for s in segs)


_QUOTE = re.compile(r"“([^”]*)”")            # 原文对话一律用「“……”」（约束 8 实测）
_LEAD_BREAK = re.compile(r"[。！？\n]")       # 引导语只取紧贴引号的那一句


class Candidate(NamedTuple):
    """一条候选：本角色的那句（`lead`/`line`）与紧邻的上一句（`prev_*`，可能是对方说的）。"""

    n: int
    lead: str
    line: str
    prev_lead: str
    prev_line: str


def extract_candidates(text: str, names: Sequence[str]) -> list[Candidate]:
    """抽出引导语里出现 `names` 中任一人名的对话句，按出场顺序编号（1 起）。

    引导语取引号前**最后一个句读之后**的那一截，不取整段叙述：一段叙述里可能先提了
    别人（「贾母听了，也笑了。宝玉道：“……”」），整段看会把人错记成贾母。

    引导语含人名即收 —— 含两个人名的引导语（约 15%：「凤姐忙和刘姥姥摆手道：」，说话
    的是凤姐）会因此多收一条错归属的候选。这里宁可多收不可漏：漏掉的是整句台词，多收
    的只是靠后的一个编号，而**归属对不对由模型读完整引导语确认后再挑**（产品侧按编号
    挑选的取舍），故 `render_candidates` 必须把引导语一并给模型看。

    无引导语的裸引号（约 14%）归不到人，给不出说话人，不进候选。每项带上上一句 ——
    对话示例要成对呈现，模型得看见对方那句才判得出这一问一答算不算「体现角色说话
    风格的交互」。
    """
    wanted = [n for n in names if n]
    quotes = list(_QUOTE.finditer(text))
    out: list[Candidate] = []
    for i, m in enumerate(quotes):
        lead = _lead_before(text, quotes, i, m.start())
        if not any(name in lead for name in wanted):
            continue
        if i == 0:
            prev_lead = prev_line = ""
        else:
            prev = quotes[i - 1]
            prev_lead = _lead_before(text, quotes, i - 1, prev.start())
            prev_line = prev.group(1)
        out.append(Candidate(n=len(out) + 1, lead=lead, line=m.group(1),
                             prev_lead=prev_lead, prev_line=prev_line))
    return out


def _lead_before(text: str, quotes: list, i: int, start: int) -> str:
    """第 i 个引号之前、离它最近的那截引导语（上一个引号结尾到此引号之间）。"""
    gap_start = quotes[i - 1].end() if i else 0
    return _LEAD_BREAK.split(text[gap_start:start])[-1].strip()


def render_candidates(candidates: Sequence[Candidate]) -> str:
    """把候选渲染成模型要读的编号块 —— 编号就是这个 `n`，调用方按同一个 `n` 取原文。

    提示词里的编号与「按编号复制」的取数共用这份结构，不另写一份带编号的文本：两份
    编号一旦错位，挑的是这句、复制的是那句，而且验收看不出来（两句都是原文里的）。
    """
    blocks = []
    for c in candidates:
        prev = f"{c.prev_lead}“{c.prev_line}”" if c.prev_line else "（无上一句）"
        blocks.append(f"[{c.n}] 上一句：{prev}\n"
                      f"    本句：{c.lead}“{c.line}”")
    return "\n".join(blocks)
