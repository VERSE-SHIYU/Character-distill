# -*- coding: utf-8 -*-
"""P15 省略号拼接档的**补充判据** —— 刻意单开一个文件，不并进 `test_phase_anchoring.py`。

**为什么单开。** `tests/test_phase_anchoring.py` 是**两个**变异驱动
（`arc_phase_anchoring_mutations.py`、`card_draft_mutations.py`）的覆盖域（见各驱动
`domain_of(GROUPS)`）。覆盖闭合（`tests/test_lock_coverage.py`）要求域内**每条判别器都被
某条变异撞过**；往那个文件里新加一条 `assert` 只会落到两种坏结局之一：
  - 被某条变异撞到 → 那条变异的红源集合变化 → 改写入库的 `*_red_lines.json` 产物；
  - 没人撞到 → 「现场有、名单没有」当场红，而它又**不能**登记进 `lock_coverage_gaps`
    （建档基线锁禁新条目）。

两条都会动到入库产物。故把这条判据放进**不被任何驱动当靶子**的文件：覆盖域不变、产物不变、
闭合照常通过，判据本身照常被 pytest 执行。`_SRC2` 等夹具与主文件同结构（两段、段 2 内两段
长片段同段按序），便于对照阅读。
"""
from __future__ import annotations

from core import phase_anchoring

# 与 tests/test_phase_anchoring.py 的 _SRC2 同构：段 2 里「次段丙…」@24、「尾段丁…」@42
_SRC2 = ("开头甲甲甲甲甲甲甲甲甲甲，中间乙乙乙乙乙乙乙乙乙乙。\n"
         "次段丙丙丙丙丙丙丙丙丙丙，多余的插入语，尾段丁丁丁丁丁丁丁丁丁丁。")
_P2_HEAD = "次段丙丙丙丙丙丙丙丙丙丙"      # 段 2 @24，规范化后 12 字
_P2_TAIL = "尾段丁丁丁丁丁丁丁丁丁丁"      # 段 2 @42，规范化后 12 字


def _phases(n, anchors=None):
    return [{"label": f"L{i}", "state": f"S{i}", "anchor": (anchors or {}).get(i, "")}
            for i in range(1, n + 1)]


def _ranges_for(anchor):
    """两阶段草稿：阶段 1 空锚点从 0 起，阶段 2 用给定锚点 → (各阶段区间, 跳过原因)。"""
    ph = phase_anchoring.build_anchors(_phases(2, {2: anchor}), _SRC2)
    return ph.ranges, ph.reason


def test_three_segment_anchor_short_middle_is_dropped():
    """三段锚点「长……短……长」：短中段（< `MIN_ELIDED_FRAGMENT_LEN`=10 字）被丢掉、
    **内容不参与核对**，整条退化成两段的拼接核对 → 通过，起点取首段（24）。

    中段刻意用**全文里根本不存在**的字（戊戊戊）：若它参与核对，一定核对不上、整条该失败；
    判通过恰好证明它被丢了。这是与参考实现（`min_fragment_len` 是 structural floor，防
    「碰巧也匹配得上」的短片段）一致的**预期软化**，不是缺陷（spec §3.1 / §9.8）。
    """
    anchor = _P2_HEAD + "……戊戊戊……" + _P2_TAIL
    assert _ranges_for(anchor) == ([(0, 24), (24, 54)], "")
