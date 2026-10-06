# -*- coding: utf-8 -*-
"""按原文位置校正「做法属于哪个阶段」—— 纯计算，无 IO。

spec：`docs/specs/arc-phase-anchoring.md` §3。模型标阶段时，阶段边界与章节不对齐就会标错
（孔乙己「下回还清」被放进顶层、阿Q「偷萝卜」被放进更晚的阶段）。本模块让每条标注都带一段
**该阶段里**的原文摘录，由代码按摘录在原文中的位置检查标注是否成立，不成立的去掉。

只认识草稿一个形状：读「阶段锚点 → 阶段范围」「摘录位置 → 标注是否成立」，回一份
`Verification`。不认识 LLM、不落卡、不改草稿 —— 归到哪一层由 `core.card_draft.card_from_draft`
按这份结果算。匹配与规范化一律复用 `core.quotes`（不在这里另写一份）。
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from core.quotes import locate_in_normalized, normalize

logger = logging.getLogger(__name__)

# 摘录最长一段在原文中的出现次数上限：超过它就不作位置证据（按该段「核对不上」处理）。
# 样本实测（公版《孔乙己》两张卡 14 段摘录）出现次数全部为 1 或 2，3 取样本最大值之上一档；
# 台词在全书反复出现时（如「我真傻，真的」）会超限 → 走兜底 D5。**全仓只此一处定义**。
MAX_OCCURRENCES = 3


class Verification(NamedTuple):
    """位置检查结果：每条条目的最终阶段、每阶段落卡用的摘录，以及监测计数。

    `phases` / `phase_quotes` 与传入的条目列表等长（按序号对应）。`phase_quotes[i]`
    是第 i 条条目在每个最终阶段下该用的 `source_quote`（顶层不用它，取第一条摘录）。
    `starts` 是各阶段起点在规范化原文里的位置（整卡跳过 / 无阶段时 None）。计数口径见
    spec §3.2 规则 6 —— 由 `card_from_draft` 打监测行（本模块不打，避免日志出处分家）。
    """
    phases: list[list[int]]
    phase_quotes: list[dict[int, str]]
    skipped_card: bool
    tags: int
    dropped: int
    ambiguous: int
    unverified_quotes: int
    fallback: int
    starts: list[int] | None


def _anchor_of(phase) -> str:
    """阶段对象的锚点文本（草稿是 `DraftPhase`，测试里也用字典）。"""
    return getattr(phase, "anchor", "") if not isinstance(phase, dict) else phase.get("anchor", "")


def _situation_label(item) -> str:
    """条目的识别文本：做法取 `situation`，记忆取 `memory`（警告文案里点出是哪一条）。"""
    return getattr(item, "situation", "") or getattr(item, "memory", "")


def _first_quotes(by_phase: dict[int, list[str]], phases: list[int]) -> dict[int, str]:
    """每个阶段取**第一条**摘录（兜底 / 整卡跳过时用；是否逐字由落卡后的核对照常把关）。"""
    return {p: by_phase[p][0] for p in phases if by_phase.get(p)}


def phase_ranges(phases, source_norm: str) -> tuple[list[tuple[int, int]], str]:
    """各阶段在规范化原文里的半开区间 `[起, 止)`，以及整卡跳过的原因（空串 = 不跳过）。

    阶段 k 的范围是 `[锚点 k 位置, 锚点 k+1 位置)`：锚点取最长段在全文的**首次**出现；阶段 1
    锚点为空时从 0 开始；最后一阶段到全文末尾。
    以下任一情况返回空表与原因，由调用方整卡跳过（规则 2 / D7）：阶段 1 以外的锚点为空（与
    「查不到」同为不可定位）、锚点核对不上、锚点位置不严格递增。
    """
    starts: list[int] = []
    for i, phase in enumerate(phases):
        anchor = _anchor_of(phase)
        if not anchor:
            if i == 0:
                starts.append(0)
                continue
            return [], f"阶段 {i + 1} 锚点为空"
        loc = locate_in_normalized(source_norm, anchor)
        if loc is None:
            return [], f"阶段 {i + 1} 锚点核对不上：{anchor}"
        starts.append(loc[0])
    for a, b in zip(starts, starts[1:]):
        if b <= a:
            return [], f"锚点位置不严格递增：{a} → {b}"
    n = len(phases)
    ranges = [(starts[k], starts[k + 1] if k + 1 < n else len(source_norm))
              for k in range(n)]
    return ranges, ""


def verify(items, valid_rows: list[list[int]], phases, source_text: str, *,
           kind: str = "做法", label=_situation_label, name: str = "",
           warn_skip: bool = True) -> Verification:
    """按原文位置校正每条条目的阶段 —— **对任意带 `occurrences` 的条目列表**工作。

    做法（`DraftBehavior`）与记忆（`DraftMemory`）各调一次：两者的位置检查口径完全一样，
    差异只在监测/警告文案里的 `kind` 与 `label`。「整卡跳过」是卡级的，调用方对第二类条目
    传 `warn_skip=False` 免得同一原因打两条；没有条目时也不打（这里根本没检查过东西）。

    `valid_rows`：`card_from_draft` 先做规则 0（编号合法性过滤）后，每条条目剩下的合法阶段编号
    （升序、去重）。本函数只对合法编号做位置检查；顺序、兜底、取值口径见 spec §3.2。
    """
    n = len(phases)
    if n == 0:                                   # 规则 5：无阶段的卡不做位置检查
        return Verification([[] for _ in items], [{} for _ in items],
                            False, 0, 0, 0, 0, 0, None)

    source_norm = normalize(source_text)
    ranges, reason = phase_ranges(phases, source_norm)
    skipped = bool(reason)
    starts = None if skipped else [r[0] for r in ranges]
    if skipped and items and warn_skip:          # 规则 2：保留模型标注，warning 一条
        logger.warning("整卡跳过位置检查（%s）：%s", reason, name)

    final: list[list[int]] = []
    phase_quotes: list[dict[int, str]] = []
    tags = dropped = ambiguous = unverified = fallback = 0

    for item, valid in zip(items, valid_rows):
        by_phase: dict[int, list[str]] = {}
        for occ in item.occurrences:
            by_phase.setdefault(occ.phase, []).append(occ.quote)

        if not valid:                            # 规则 0 已整条撤回
            final.append([])
            phase_quotes.append({})
            continue
        if skipped:                              # 规则 2：保留标注，摘录取首段
            tags += len(valid)                   # 分母照常计（监测比例要用）
            final.append(list(valid))
            phase_quotes.append(_first_quotes(by_phase, valid))
            continue

        standing: list[int] = []
        chosen: dict[int, str] = {}
        for p in valid:                          # 计数单位是标注（条目, 阶段），不是摘录
            tags += 1
            picked = ""
            for quote in by_phase.get(p, []):    # 规则 3：同阶段多段摘录，任一段通过即成立
                loc = locate_in_normalized(source_norm, quote)
                if loc is None or len(loc) > MAX_OCCURRENCES:
                    unverified += 1              # 查不到 / 出现太多 → 不作位置证据
                    continue
                lo, hi = ranges[p - 1]
                if any(lo <= x < hi for x in loc):
                    if len(loc) > 1:
                        ambiguous += 1           # D6：任意一次命中即通过，记「位置不唯一」
                    picked = quote
                    break
            if picked:
                standing.append(p)
                chosen[p] = picked
            else:                                # 规则 3：去掉的标注各打一条 warning
                dropped += 1
                logger.warning("去掉标注：%s %r 在阶段 %d 没有落在该阶段的摘录",
                               kind, label(item), p)

        if not standing:                         # 规则 4：全部被去掉 → 兜底，退回合法标注
            fallback += 1
            logger.warning("兜底：%s %r 的标注全部被去掉，退回原标注 %s",
                           kind, label(item), valid)
            final.append(list(valid))
            phase_quotes.append(_first_quotes(by_phase, valid))
        else:
            final.append(standing)
            phase_quotes.append(chosen)

    return Verification(final, phase_quotes, skipped, tags, dropped, ambiguous,
                        unverified, fallback, starts)
