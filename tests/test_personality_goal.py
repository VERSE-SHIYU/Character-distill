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
G3（验收 3 的同卡部分）全程做到亲近条件能进亲近档，且不早于第 3 次；门槛只管第一次，到过亲近档的不再受限；
    负对照：把「做到亲近条件」换成「冒犯」，G3 的判定必须失败。
G5 模型给的数被档位卡住：报小档却给大数，每轮最多变 2；报大档最多变 8；档内的数原样生效（Q12）。
G6 正向大档要前面连续 2 轮非负才生效，否则按中档算（设计稿 §5）。
G9 卡上没有新字段（或三档没填全）时，人格块仍是改造前的文案 —— 段 1 上线后所有旧卡都走这条路。
G10 评估 prompt：性格特征与价值观各归各位；不再有与新规则矛盾的旧句子；记仇只进 prompt，代码不读。
G11 用默认亲近条件的卡：门槛同为 3 次；单轮最多涨 5（设计稿 Q4）。
G12 三档做法、亲近条件可以只在某个阶段成立：选了那个阶段才用它，别的阶段用全程的。
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


def _raw(name: str, *, own_conditions: bool = True, overlay: bool = True,
         psyche_overrides: dict | None = None, phase_overlay: tuple[int, dict] | None = None) -> dict:
    raw = json.loads((_SAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    if overlay:
        raw["psyche"].update(json.loads(json.dumps(_FIX[name], ensure_ascii=False)))
    if not own_conditions:
        raw["psyche"]["warming_conditions"] = []
    for path, value in (psyche_overrides or {}).items():
        node = raw["psyche"]
        *parents, leaf = path.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
    if phase_overlay:
        k, psyche_at_k = phase_overlay
        raw["character_arc"]["phases"][k - 1]["overlay"] = {"psyche": psyche_at_k}
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


def _run_full(name: str, start: int, script, *, resume_from: dict | None = None,
              arc_phase: int | None = None, **card_kw):
    """同 `_run`，另外返回每轮发给评估模型的 prompt。`resume_from` 给了就从这份落库数据接着聊。"""
    llm = _ReplayLLM(script)
    eng = ChatEngine(llm=llm, rag=None, card=CharacterCard.model_validate(_raw(name, **card_kw)), card_id="c",
                     storage=None, session_id="", is_new_session=False, arc_phase=arc_phase)
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
    llm.engine = eng
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


def _g3_check(name: str, start: int, event: str, *, exact: bool = False, **card_kw):
    prompts, aff, stage = _run(name, start, [(event, "large", 8)] * _TURNS, **card_kw)
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
    _g3_check(name, start, "met_condition", exact=start >= 68, own_conditions=False)     # G11


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


def test_g9_card_without_new_fields_keeps_the_old_persona_text():
    # 赵太爷：好感 20 落在「认识」档，宜人性 1 —— 改造前就是这两句
    (sp, *_), _, _ = _run("赵太爷", 20, [("neutral", "small", 1)], overlay=False)
    assert "客气有距离，礼貌但疏远" in sp and "你天生有保留" in sp


def test_g9_incomplete_modes_fall_back_to_the_generic_tone():
    name = "赵太爷"
    (sp, *_), _, _ = _run(name, 20, [("neutral", "small", 1)],
                          psyche_overrides={"relational_modes.conflict": ""})
    assert "客气有距离，礼貌但疏远" in sp, "三档没填全，却没有退回通用语气"
    assert _modes(name)["normal"] not in sp, "三档没填全，却用了其中一句"
    (sp, *_), _, _ = _run(name, 20, [("neutral", "small", 1)],
                          psyche_overrides={"agreeableness_facets": []})
    assert "你天生有保留" in sp, "没有分面，却没有退回按分数的文案"


@pytest.mark.parametrize("name", _CARDS)
def test_g10_eval_prompt_labels_traits_and_values_correctly(name):
    raw = _raw(name)
    ep = _run_full(name, 40, [("neutral", "small", 1)])[3].eval_prompts[0]
    line = {k: next((ln for ln in ep.splitlines() if ln.startswith(k)), "") for k in ("性格特征：", "价值观：")}
    assert raw["personality_traits"][0][:6] in line["性格特征："], "「性格特征」后面不是 personality_traits"
    assert raw["values"][0][:6] not in line["性格特征："], "「性格特征」后面还是 values"
    assert raw["values"][0][:6] in line["价值观："], "缺「价值观」一行"


_STALE_EVAL_LINES = [      # 与「好感由规则表算、没有回落」矛盾的旧句子
    "连续3轮正面互动才能触发阶段性好感跃升", "自然回到基线附近", "好感围绕这条基线波动",
    "基线上移要慢", '"affinity": 0-100整数',
]


def test_g10_eval_prompt_has_no_stale_rules_and_carries_grudge():
    one = [("neutral", "small", 1)]
    ep = {v: _run_full("赵太爷", 40, one, psyche_overrides={"grudge_inertia": v})[3].eval_prompts[0]
          for v in ("记仇", "大度")}
    for line in _STALE_EVAL_LINES:
        assert line not in ep["记仇"], f"评估 prompt 里还有旧句子：{line}"
    assert "不要太快接受道歉" in ep["记仇"]
    # 说明文案里本来就有「记仇=…，大度=…」，所以要看的是这张卡自己的取值（方括号里那个）
    assert "【记仇】" in ep["记仇"] and "【大度】" not in ep["记仇"], "评估 prompt 没带这张卡的记仇程度"
    assert "【大度】" in ep["大度"] and "【记仇】" not in ep["大度"]


def test_g10_code_does_not_read_grudge_or_volatility():
    script = [("offended", "medium", 4), ("repair", "small", 2), ("repair", "medium", 4),
              ("met_condition", "large", 8), ("friendly", "small", 2)]
    runs = [_run("孔乙己", 50, script, psyche_overrides=o)[1] for o in (
        {"grudge_inertia": "记仇", "volatility": "剧烈"}, {"grudge_inertia": "大度", "volatility": "平稳"})]
    assert runs[0] == runs[1], f"同一组评估输出，记仇 / 大度的卡算出了不同的好感：{runs}"


def test_g11_default_condition_card_rises_at_most_five_per_turn():
    script = [("neutral", "small", 1)] * 2 + [("met_condition", "large", 8)] * 3
    for own, cap in ((False, 5), (True, _TIER_CAP["large"])):
        _, aff, _ = _run("赵太爷", 20, script, own_conditions=own)
        steps = [b - a for a, b in zip([20] + aff, aff)][2:]
        assert max(steps) == cap, f"own_conditions={own}：单轮最多应涨 {cap}，实际 {steps}"


def test_g12_phase_limited_mode_and_condition_apply_only_in_that_phase():
    """语义 2026-10-09 改为「沿用最近一次」，见 docs/specs/state-inertia.md。

    三档 close：阶段 2 有全程（顶层）close，按规则 3「k 上有证据的优先」，阶段 1 专属的 close
    不进阶段 2。亲近条件（状态类列表）：阶段 2 没有自己那格，沿用阶段 1 → 阶段 2 的评估
    prompt 里也看得到它。
    """
    name = "孔乙己"                               # 样本卡有两个阶段
    only_phase_1 = (1, {"relational_modes": {"close": "只在第一阶段：把最后一颗豆也让给对方"},
                        "warming_conditions": ["只在第一阶段：对方请他喝了一碗酒"]})
    seen = {}
    for k in (1, 2):
        prompts, _, _, llm = _run_full(name, 80, [("neutral", "small", 1)], arc_phase=k,
                                       phase_overlay=only_phase_1)
        seen[k] = (prompts[0], llm.eval_prompts[0])
    line, cond = only_phase_1[1]["relational_modes"]["close"], only_phase_1[1]["warming_conditions"][0]
    assert line in seen[1][0] and _modes(name)["close"] not in seen[1][0], "选了第一阶段，却没用这个阶段的亲近档做法"
    assert cond in seen[1][1], "选了第一阶段，评估 prompt 里没有这个阶段的亲近条件"
    assert line not in seen[2][0] and _modes(name)["close"] in seen[2][0], "第二阶段用到了第一阶段才有的做法"
    assert cond in seen[2][1], "第二阶段没有自己的亲近条件，应沿用第一阶段的那条"


def test_g8_rule_state_survives_the_catchword_save():
    """反思触发后引擎会单独存一次口头禅；这次保存不能把规则状态抹掉。"""
    name, met = "孔乙己", ("met_condition", "large", 8)
    *_, llm = _run_full(name, 70, [met] * 2)
    eng, saved = llm.engine, {}
    eng._storage, eng._session_id = object(), "s"
    eng._save_affinity_state = lambda: saved.update(eng.get_affinity())
    eng._persist_catchwords()
    assert saved, "夹具前提不成立：口头禅这条保存路径没有走到"
    _, aff, _ = _run(name, 0, [met], resume_from=json.loads(json.dumps(saved, ensure_ascii=False)))
    assert aff[0] >= _CLOSE_FROM, f"口头禅保存之后次数丢了：第 3 次没能进亲近档（{aff}）"


_DROP_OUT = [("offended", "large", 8)] * 2          # 82 → 66：掉出亲近档


def test_g4_repair_restores_a_relationship_that_started_close():
    """起点就在亲近档的关系（恋人、家人）被冒犯掉出亲近档后，修复能回到原处，不用重新挣 3 次。"""
    name, start = "孔乙己", 82
    _, aff, _ = _run(name, start, _DROP_OUT + [("repair", "medium", 5)] * 6)
    assert aff[1] < _CLOSE_FROM, f"夹具前提不成立：两次冒犯后应掉出亲近档（{aff}）"
    assert max(aff[2:]) <= start and aff[-2:] == [start, start], f"修复没能回到并停在冒犯前的 {start}：{aff}"


def test_g3_gate_only_guards_the_first_entry():
    """到过亲近档的关系掉下去后，做到一次亲近条件就能回去；从没到过的，门槛照旧。"""
    name, met = "孔乙己", ("met_condition", "medium", 5)
    _, aff, _ = _run(name, 82, _DROP_OUT + [("neutral", "small", 1)] * 2 + [met] * 2)
    assert aff[3] < _CLOSE_FROM <= aff[5], f"到过亲近档的关系没能靠做到亲近条件回去：{aff}"
    _, aff, _ = _run(name, 66, [met] * 2)
    assert max(aff) < _CLOSE_FROM, f"从没到过亲近档，两次就进去了：{aff}"


@pytest.mark.parametrize("shape", ["single", "group"])
def test_g8_having_been_close_survives_a_new_engine(shape):
    name, met = "孔乙己", ("met_condition", "medium", 5)
    *_, llm = _run_full(name, 82, _DROP_OUT)
    assert llm.saved["affinity"] < _CLOSE_FROM, "夹具前提不成立：应已掉出亲近档"
    _, aff, _ = _run(name, 0, [met] * 2, resume_from=_saved(llm, shape))
    assert aff[-1] >= _CLOSE_FROM, f"换引擎后忘了这段关系到过亲近档：{aff}"
