# -*- coding: utf-8 -*-
"""D24 ⑥ 目标检查：attitude / note 不写主角名。

根因：关系生成口径第 8 条只给 relation、note 定了人称，attitude 没有；判据
`relation_violation` 只查 relation，补跑筛选与补跑后复核都只看它。云绮卡重蒸后
attitude 里 11 处写「云绮」（另 6 处用「她」指主角，规则判不了，只靠提示词）。

这里锁：口径写进第 8 条；整条关系（relation / attitude / note / attitudes[] 的
attitude 与 note）任一处写主角名即不合口径，与 relation 违规合成同一次补跑；
quote 是原文摘录不查；本批里含主角名的别人的名字（甄宝玉 ⊃ 宝玉）不误报；
「她」指对方不误报。
"""
from __future__ import annotations

import logging

from core.relationship_batch import batch_relationships

_NOTE = "本角色弧线的阶段（略）。"


def _ok(target: str, **over) -> dict:
    row = {"target": target, "relation": "友", "attitude": "信得过", "note": "我的朋友",
           "attitudes": [{"phase": 1, "attitude": "信得过", "quote": "原文摘录", "note": "我的朋友"}]}
    row.update(over)
    return row


def _run(first: dict[str, dict], *, name: str = "云绮", targets=None, retry_fix=True):
    """第 1 次回 `first`（按人），补跑回合规条目（retry_fix=False 时补跑仍回原样）。"""
    calls: list[tuple[list[str], str]] = []
    targets = targets or list(first)

    def fake(system: str, user: str, batch: list[str]):
        calls.append((list(batch), system))
        if len(calls) == 1 or not retry_fix:
            return [first[t] for t in batch]
        return [_ok(t) for t in batch]

    out = batch_relationships(targets, _NOTE, stream_call=fake, prefix="P", name=name)
    return calls, out


def test_rules_require_first_person_in_attitude():
    from core.relationship_batch import RELATIONSHIP_RULES

    rule8 = RELATIONSHIP_RULES.split("8.", 1)[1]
    assert "attitude" in rule8 and "主角名" in rule8


def test_top_attitude_with_main_name_is_retried():
    calls, out = _run({"兰香": _ok("兰香", attitude="云绮二话不说就是两记耳光"), "甲": _ok("甲")})
    assert len(calls) == 2 and calls[1][0] == ["兰香"]
    assert "第 8 条" in calls[1][1] and "兰香" in calls[1][1]
    assert {r["target"]: r["attitude"] for r in out}["兰香"] == "信得过"


def test_phase_attitude_with_main_name_is_retried():
    bad = _ok("阿生", attitudes=[{"phase": 2, "attitude": "云绮看他好笑", "quote": "原文", "note": "我"}])
    calls, _ = _run({"阿生": bad})
    assert len(calls) == 2 and calls[1][0] == ["阿生"]


def test_note_with_main_name_is_retried():
    calls, _ = _run({"明昭": _ok("明昭", note="明昭是云绮挑来的人")})
    assert len(calls) == 2 and calls[1][0] == ["明昭"]


def test_still_violating_after_retry_warns_but_keeps(caplog):
    with caplog.at_level(logging.WARNING, logger="core.relationship_batch"):
        calls, out = _run({"兰香": _ok("兰香", attitude="云绮扇她耳光")}, retry_fix=False)
    assert len(calls) == 2
    assert out[0]["attitude"] == "云绮扇她耳光"
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(msgs) == 1 and "兰香" in msgs[0] and "云绮" in msgs[0]


# ── 不误报（改前改后都该绿：普通对照）──────────────────────────────────

def test_quote_with_main_name_is_not_flagged():
    row = _ok("吴大娘", attitudes=[{"phase": 2, "attitude": "敬她", "quote": "云绮笑道：您才是菩萨心肠", "note": "我敬她"}])
    calls, _ = _run({"吴大娘": row})
    assert len(calls) == 1


def test_she_for_female_target_is_not_flagged():
    calls, _ = _run({"萧兰淑": _ok("萧兰淑", attitude="她下毒毁我容貌，我便将计就计", note="她恨我")})
    assert len(calls) == 1


def test_other_name_containing_main_name_is_not_flagged():
    calls, _ = _run({"甄宝玉": _ok("甄宝玉", attitude="与我同名同貌，见了甄宝玉便觉无趣"), "袭人": _ok("袭人")},
                    name="宝玉")
    assert len(calls) == 1
