# -*- coding: utf-8 -*-
"""③ 目标检查（设计稿 `personality-3a-design.md` v6 §9）：性格注入与好感机制。

目标：原著里不好相处的角色，聊天时不被拉回「好说话」；关系真的变近后，在角色自己的
范围内变暖。对应验收三条里代码能保证的部分（跨角色快慢靠评估模型的判断，演示卡验收时人读）。

走真实 `ChatEngine`：聊天、`_post_turn`、评估管道都不打桩，只换 LLM。桩 LLM **只回放
标签序列**（2609.00982 的回放审计），不模拟评估模型怎么判。观测的是引擎真正发给模型的
system prompt 与引擎对外的好感状态（`get_affinity()`）。

预言独立于被测代码：必须出现 / 不得出现的句子直接从夹具 JSON 取；数值断言只用夹具里的
基线，以及设计稿里已定的几个数（亲近档下界 73、门槛 3、档位上限 2 / 5 / 8、默认亲近条件），
都写成本文件里的字面量，不从被测代码 import，不调规则模块。

G0 夹具自检：各卡各档的句子、分面行为两两不同，否则 G1 空转。
G1（验收 1）同一档位下，prompt 里只有这张卡自己这一档的做法和分面。
G2（验收 2）全程闲聊 / 客气 / 冒犯，始终进不了亲近档；客气最多涨到基线。
G3（验收 3 的同卡部分）全程做到亲近条件能进亲近档，且不早于第 3 次；
    负对照：把「做到亲近条件」换成「冒犯」，G3 的判定必须失败。
G5 模型给的数被档位卡住：报小档却给大数，每轮最多变 2；报大档最多变 8；档内的数原样生效（Q12）。
G6 正向大档要前面连续 2 轮非负才生效，否则按中档算（设计稿 §5）。
G8 换一个引擎接着聊（单聊存档、群聊行两种落库形态），次数、待修复的冒犯、冲突档都还在。
G7 评估时看的是这张卡自己的亲近条件；卡上没有才用默认两条；编号对不上的「做到亲近条件」不算数。
G4 转折：冒犯后下一轮换成冲突档（优先于亲近档）；不再冒犯就退出冲突档；
    道歉修复不超过冒犯前的值。

旧协议对照：main 上评估要模型给好感**绝对值**、代码只截断单轮变化
（`affinity_service.py:347`）。为了让同一份检查能在 main 上跑出有意义的红，桩在同一个 JSON 里
按事件方向附上 `affinity`（当前值 ±8，即旧代码允许的上限）；新协议不读这个键。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.chat_engine import ChatEngine
from core.schema import CharacterCard

_ROOT = Path(__file__).resolve().parent.parent
_SAMPLES = _ROOT / "docs" / "specs" / "arc-behaviors-draft-samples"
_FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "personality_goal.json")
                  .read_text(encoding="utf-8"))
_CARDS = [k for k in _FIX if not k.startswith("_")]

_CLOSE_FROM = 73          # 亲近档下界
_MET_REQUIRED = 3         # 进亲近档要累计几次「做到亲近条件」
_TURNS = 20
_EVAL_SYS = "你是精确的JSON输出器"
_POS, _NEG = {"met_condition", "friendly", "repair"}, {"offended", "trigger"}

# main 上所有角色共用的 6 档语气与写死的宜人性文案：有三档做法、有分面的卡不应再出现
_GENERIC_TONES = [
    "戒备疏离，不主动，不信任", "客气有距离，礼貌但疏远", "自然但不交心",
    "愿聊会关心，会开玩笑", "亲密主动，话变多", "不设防，完全信任",
]
_GENERIC_AGREEABLENESS = ["你天生好说话", "你天生有保留", "你有自己的社交节奏"]
# 卡上没有亲近条件时用的默认两条（设计稿 §5，Q7）
_DEFAULT_WARMING = ["对方向你说了自己的心事", "对方认真回应了你说的事"]
_TIER_CAP = {"small": 2, "medium": 5, "large": 8}      # 档位上限（设计稿 Q12）


def _raw(name: str, *, own_conditions: bool = True) -> dict:
    raw = json.loads((_SAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    raw["psyche"].update(_FIX[name])
    if not own_conditions:
        raw["psyche"]["warming_conditions"] = []
    return raw


def _modes(name: str) -> dict:
    return _FIX[name]["relational_modes"]


def _facets(name: str) -> list[str]:
    return [f["behavior"] for f in _FIX[name]["agreeableness_facets"]]


class _ReplayLLM:
    """聊天调用记下 system prompt；评估调用按脚本回放一条标签。"""
    model = "stub"

    def __init__(self, script):
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.script, self.i = list(script), 0
        self.prompts: list[str] = []
        self.eval_prompts: list[str] = []
        self.engine = None

    def preflight(self):
        return None

    def chat(self, system_prompt, messages, **_kw):
        if system_prompt.startswith(_EVAL_SYS):
            event, tier, delta, *rest = self.script[self.i]
            self.i += 1
            self.eval_prompts.append(messages[0]["content"])
            cur = self.engine.get_affinity()["affinity"]
            legacy = cur + (8 if event in _POS else -8 if event in _NEG else 0)
            return json.dumps({"affinity_event": event, "affinity_tier": tier,
                               "affinity_delta": delta,
                               "met_condition_index": rest[0] if rest else 0,
                               "affinity": max(0, min(100, legacy))}, ensure_ascii=False)
        self.prompts.append(system_prompt)
        return "回复"


def _run(name: str, start: int, script, **card_kw):
    """返回 (每轮发出的 system prompt, 每轮评估后的好感, 每轮评估后的档名)。"""
    prompts, aff, stage, _ = _run_full(name, start, script, **card_kw)
    return prompts, aff, stage


def _run_full(name: str, start: int, script, *, resume_from: dict | None = None, **card_kw):
    """同 `_run`，另外返回每轮发给评估模型的 prompt。`resume_from` 给了就从这份落库数据接着聊。"""
    llm = _ReplayLLM(script)
    eng = ChatEngine(llm=llm, rag=None, card=CharacterCard.model_validate(_raw(name, **card_kw)), card_id="c",
                     storage=None, session_id="", is_new_session=False, arc_phase=None)
    llm.engine = eng
    eng.load_affinity(resume_from or {"affinity": start, "trust": 30, "mood": "平静", "guard": 70,
                                      "reason": "", "inner_voice": ""}, initialized=True)
    aff, stage = [], []
    for i in range(len(llm.script)):
        eng.chat(f"第{i + 1}句")
        state = eng.get_affinity()
        aff.append(state["affinity"])
        stage.append(state["stage"])
    assert len(llm.prompts) == len(llm.script) == llm.i, "有一轮没发聊天调用或没跑评估"
    llm.saved = eng.get_affinity()          # 单聊落库的就是这份（`to_persist` 序列化它）
    return llm.prompts, aff, stage, llm


def test_g0_fixture_sentences_are_distinct():
    lines = [s for n in _CARDS for s in list(_modes(n).values()) + _facets(n)]
    assert len(_CARDS) >= 2 and all(lines), "夹具缺卡或有空句子"
    assert len(set(lines)) == len(lines), "夹具里有重复的句子 —— G1 的「不得出现」空转"
    assert not any(a != b and a in b for a in lines for b in lines), "夹具句子互为子串"


@pytest.mark.parametrize("name", _CARDS)
@pytest.mark.parametrize("start", [20, 60, 80])
def test_g1_same_level_keeps_the_character(name, start):
    tier = "close" if start >= _CLOSE_FROM else "normal"
    (sp, *_), _, _ = _run(name, start, [("neutral", "small", 1)])
    assert _modes(name)[tier] in sp, f"好感 {start}：缺这张卡「{tier}」档的做法"
    for b in _facets(name):
        assert b in sp, f"缺分面行为：{b}"
    for other_tier, line in _modes(name).items():
        if other_tier != tier:
            assert line not in sp, f"好感 {start}：出现了「{other_tier}」档的做法"
    for other in _CARDS:
        if other != name:
            for line in list(_modes(other).values()) + _facets(other):
                assert line not in sp, f"出现了别的卡（{other}）的句子"
    for line in _GENERIC_TONES + _GENERIC_AGREEABLENESS:
        assert line not in sp, f"仍在用所有角色共用的文案：{line}"


_G2 = [  # (卡, 起点, 事件, 档, 整数)
    ("赵太爷", 15, "neutral", "small", 1),
    ("赵太爷", 15, "friendly", "large", 8),     # 评估偏宽松：把客气报成大档
    ("孔乙己", 50, "friendly", "large", 8),     # 起点已在基线以上
    ("赵太爷", 15, "offended", "medium", 4),
]


@pytest.mark.parametrize("name,start,event,tier,delta", _G2,
                         ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in _G2])
def test_g2_low_affinity_does_not_soften(name, start, event, tier, delta):
    baseline = _raw(name)["psyche"]["affinity_baseline"]
    prompts, aff, stage = _run(name, start, [(event, tier, delta)] * _TURNS)
    assert all(a < _CLOSE_FROM for a in aff), f"{event} × {_TURNS} 轮进了亲近档：{aff}"
    assert not any(s in ("亲近", "心意相通") for s in stage)
    for sp in prompts:
        assert _modes(name)["close"] not in sp, "亲近档的做法出现在 prompt 里"
    if event not in _NEG:
        for sp in prompts:
            assert _modes(name)["normal"] in sp, f"{event}：这一轮不是平常档"
            assert _modes(name)["conflict"] not in sp, f"{event}：没有冒犯却进了冲突档"
    if event == "neutral":
        assert aff == [start] * _TURNS, f"闲聊时好感动了：{aff}"
    if event == "friendly":
        steps = [b - a for a, b in zip([start] + aff, aff)]
        assert max(steps) <= _TIER_CAP["small"], f"客气被当成了大档：单轮 +{max(steps)}"
        assert max(aff) <= max(start, baseline), f"客气把好感推过了基线 {baseline}：{aff}"
        assert aff[-1] == max(start, baseline), f"客气没有涨到基线 {baseline}：{aff}"
    if event == "offended":
        assert all(b <= a for a, b in zip([start] + aff, aff)), f"冒犯后好感涨了：{aff}"
        assert _modes(name)["normal"] in prompts[0]
        for sp in prompts[1:]:
            assert _modes(name)["conflict"] in sp, "冒犯后的下一轮没有换成冲突档"


def _g3_check(name: str, start: int, event: str, *, exact: bool = False):
    prompts, aff, stage = _run(name, start, [(event, "large", 8)] * _TURNS)
    entered = [i for i, a in enumerate(aff) if a >= _CLOSE_FROM]
    assert entered, f"{_TURNS} 轮都做到亲近条件仍进不了亲近档：{aff}"
    first = entered[0]                     # 第 first+1 次「做到亲近条件」之后进入
    assert first + 1 >= _MET_REQUIRED, f"第 {first + 1} 次就进了亲近档：{aff}"
    if exact:        # 起点离 73 只差几分：挡住它的只有次数门槛，所以应当正好在第 3 次进入
        assert first + 1 == _MET_REQUIRED, f"门槛不是 {_MET_REQUIRED} 次：第 {first + 1} 次才进（{aff}）"
    assert stage[first] in ("亲近", "心意相通")
    if first + 1 < _TURNS:
        assert _modes(name)["close"] in prompts[first + 1], "进亲近档后下一轮没有用亲近档的做法"


@pytest.mark.parametrize("name", _CARDS)
@pytest.mark.parametrize("start", [15, 68])
def test_g3_meeting_conditions_opens_closeness_not_before_third(name, start):
    _g3_check(name, start, "met_condition", exact=start >= 68)


@pytest.mark.parametrize("name", _CARDS)
def test_g3_negative_control_swapped_labels_must_fail(name):
    with pytest.raises(AssertionError):
        _g3_check(name, 68, "offended")


def test_g4_offence_switches_to_conflict_even_when_close():
    name = "孔乙己"
    script = [("met_condition", "large", 8)] * 6 + [("offended", "medium", 4)] * 2
    prompts, aff, _ = _run(name, 68, script + [("neutral", "small", 1)])
    assert aff[6] >= _CLOSE_FROM, f"夹具前提不成立：冒犯后好感应仍在亲近档以上（{aff}）"
    assert _modes(name)["close"] in prompts[6], "冒犯发生前应是亲近档"
    for sp in prompts[7:9]:
        assert _modes(name)["conflict"] in sp, "冒犯后的下一轮没有换成冲突档"
        assert _modes(name)["close"] not in sp, "冲突档没有优先于亲近档"


def test_g4_conflict_ends_once_offence_stops():
    name = "赵太爷"
    script = [("offended", "small", 1)] * 2 + [("friendly", "small", 1)] * 2
    prompts, _, _ = _run(name, 50, script)
    assert _modes(name)["conflict"] in prompts[1] and _modes(name)["conflict"] in prompts[2]
    assert _modes(name)["conflict"] not in prompts[3], "不再冒犯后仍停在冲突档"
    assert _modes(name)["normal"] in prompts[3]


def test_g4_repair_never_exceeds_pre_offence_value():
    name, start = "孔乙己", 50
    script = [("offended", "medium", 4)] + [("repair", "large", 8)] * 5
    _, aff, _ = _run(name, start, script)
    assert aff[0] < start, "夹具前提不成立：冒犯没有降好感"
    assert max(aff[1:]) <= start, f"道歉修复超过了冒犯前的值 {start}：{aff}"
    assert aff[-1] == start, f"道歉修复没有补回到冒犯前的值 {start}：{aff}"


@pytest.mark.parametrize("tier,cap", [("small", 2), ("medium", 5), ("large", 8)])
def test_g5_model_number_is_bounded_by_its_tier(tier, cap):
    name, start = "赵太爷", 15
    for event in ("met_condition", "offended"):
        _, aff, _ = _run(name, 50 if event == "offended" else start, [(event, tier, 40)] * 6)
        steps = [abs(b - a) for a, b in zip([50 if event == "offended" else start] + aff, aff)]
        assert max(steps) <= cap, f"{event}/{tier}：模型给 40，单轮变了 {max(steps)}（上限 {cap}）"
        assert any(steps), f"{event}/{tier}：好感没有动，上限检查空转"


@pytest.mark.parametrize("event,tier,delta", [
    ("met_condition", "small", 1), ("met_condition", "small", 2), ("met_condition", "medium", 3),
    ("offended", "medium", 5), ("offended", "large", 6),
])
def test_g5_number_inside_its_tier_is_used_as_given(event, tier, delta):
    start = 40                                  # 离 0、73、100 都远，没有别的规则插手
    _, aff, _ = _run("赵太爷", start, [(event, tier, delta)] * 3)
    sign = 1 if event in _POS else -1
    assert aff == [start + sign * delta * i for i in (1, 2, 3)], f"档内的 {delta} 没有原样生效：{aff}"


def test_g6_large_positive_needs_two_non_negative_turns_first():
    start = 30
    script = [("offended", "small", 1)] + [("met_condition", "large", 8)] * 4
    _, aff, _ = _run("孔乙己", start, script)
    steps = [b - a for a, b in zip([start] + aff, aff)]
    assert steps[0] < 0
    assert max(steps[1:3]) <= _TIER_CAP["medium"], f"刚冒犯完，大档就生效了：{steps}"
    assert min(steps[3:]) > _TIER_CAP["medium"], f"连续 2 轮非负之后，大档仍没生效：{steps}"


@pytest.mark.parametrize("name", _CARDS)
def test_g7_evaluator_is_shown_the_cards_own_conditions(name):
    one = [("neutral", "small", 1)]
    evals = _run_full(name, 40, one)[3].eval_prompts
    for cond in _FIX[name]["warming_conditions"]:
        assert cond in evals[0], f"评估 prompt 里没有这张卡的亲近条件：{cond}"
    for cond in _DEFAULT_WARMING:
        assert cond not in evals[0], "卡上有亲近条件，评估 prompt 里却出现了默认条件"
    evals = _run_full(name, 40, one, own_conditions=False)[3].eval_prompts
    for cond in _DEFAULT_WARMING:
        assert cond in evals[0], f"卡上没有亲近条件，评估 prompt 里缺默认条件：{cond}"


def test_g7_condition_index_out_of_range_does_not_count():
    name, start = "孔乙己", 70                  # 起点在基线 45 以上：客气不加分
    n = len(_FIX[name]["warming_conditions"])
    _, aff, stage = _run(name, start, [("met_condition", "large", 8, n)] * 6)
    assert aff == [start] * 6, f"编号 {n} 不存在，却被当成做到了亲近条件：{aff}"
    _, aff, _ = _run(name, start, [("met_condition", "large", 8, n - 1)] * 6)
    assert aff[-1] >= _CLOSE_FROM, f"夹具前提不成立：编号合法时应能进亲近档（{aff}）"


def _saved(llm, shape: str) -> dict:
    """两种落库形态：单聊存整份状态；群聊只存 5 个标量列（`update_group_affinity`）。"""
    full = json.loads(json.dumps(llm.saved, ensure_ascii=False))
    if shape == "single":
        return full
    return {k: full[k] for k in ("affinity", "trust", "mood", "guard", "reason")}


@pytest.mark.parametrize("shape", ["single", "group"])
def test_g8_gate_count_survives_a_new_engine(shape):
    name, met = "孔乙己", ("met_condition", "large", 8)
    *_, llm = _run_full(name, 70, [met] * 2)
    assert llm.saved["affinity"] < _CLOSE_FROM, "夹具前提不成立：两次之后不该进亲近档"
    _, aff, _ = _run(name, 0, [met], resume_from=_saved(llm, shape))
    assert aff[0] >= _CLOSE_FROM, f"换引擎后次数丢了：第 3 次没能进亲近档（{aff}）"


@pytest.mark.parametrize("shape", ["single", "group"])
def test_g8_pending_offence_survives_a_new_engine(shape):
    name, start = "孔乙己", 50
    *_, llm = _run_full(name, start, [("offended", "medium", 4)])
    prompts, aff, _ = _run(name, 0, [("repair", "large", 8)] * 4, resume_from=_saved(llm, shape))
    assert _modes(name)["conflict"] in prompts[0], "换引擎后的第一轮没有停在冲突档"
    assert max(aff) <= start and aff[-1] == start, f"换引擎后修复上限丢了：{aff}（冒犯前 {start}）"
