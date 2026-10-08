"""spec `personality-inject.md` §5 对账表的变异，外加两轮独立审计里存活过的那几条、以及 2026-10-08 收尾的两条。一次性产物。

执行框架与判档不在本文件里：改文件、跑 pytest、按字节还原、基线门都用
`tests/perf/mutation_framework.py` 的 `run_oneoff`（与 `arc_phase_unlocated_*_mutations.py` 同一处置）。

用法：仓库根目录 `python docs/specs/artifacts/personality_inject_mutations.py [起 止]`
（可选的起止下标只是为了分批跑）。期望：全部 RED。
退出码：0 = 全部符合预期；1 = 有存活；2 = 基线红（拒跑）。
"""
from __future__ import annotations

import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "perf"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_framework as framework  # noqa: E402

RULES = ROOT / "core" / "affinity_rules.py"
PROTOCOL = ROOT / "core" / "affinity_protocol.py"
SERVICE = ROOT / "core" / "affinity_service.py"
SCHEMA = ROOT / "core" / "schema.py"
TARGETS = (RULES, PROTOCOL, SERVICE, SCHEMA)

GOAL = "tests/test_personality_goal.py"
UNIT = "tests/test_affinity_rules.py"
CLAMP = "tests/test_affinity_clamp.py"
CATCH = "tests/test_catchwords.py"

_TIER = '    if state.last_event in NEGATIVE:\n        return "conflict"'
_BASELINE = '    return max(affinity, psyche.affinity_baseline) if event == "friendly" else None'
_REPAIR = '    return state.pre_offence if event == "repair" else None'
_LEGACY = '    return None if psyche.warming_conditions else affinity + LEGACY_UP_MAX'
_GATE = '    blocked = affinity < CLOSE_FROM and not state.reached_close and state.met_count < MET_REQUIRED'
_SIZE = '    size = max(lo, min(hi, abs(delta)))'
_WARMING = '    return list(psyche.warming_conditions or DEFAULT_WARMING)'
_COMPLETE = '        return bool(self.close and self.normal and self.conflict)'
_PRE = '        pre_offence=affinity if negative and state.pre_offence is None else state.pre_offence,'
_PACKED = '            "relation": self.relation.to_dict(),'


def _m(label: str, target: str, path: pathlib.Path, old: str, new: str):
    return (label, target, [("repl", path, [(old, new)])], "RED")


MUTANTS = [
    _m("放宽 R2 档内整数不截断", GOAL, RULES, _SIZE, '    size = abs(delta)'),
    _m("放宽 R2 档内永远取上限", GOAL, RULES, _SIZE, '    size = hi'),
    _m("放宽 R2 好感不设下限 0", UNIT, RULES, '    new = max(0, min(100, new))', '    new = min(100, new)'),
    _m("过严 R3 闲聊向基线回落", GOAL, RULES, '    new = affinity + _change(event, tier, delta)\n',
       '    new = affinity + _change(event, tier, delta) + (event == "neutral") * '
       '(1 if affinity < psyche.affinity_baseline else -1 if affinity > psyche.affinity_baseline else 0)\n'),
    _m("放宽 R4 基线以上客气也加分", GOAL, RULES, _BASELINE, '    return None'),
    _m("过严 R4 基线以下客气不加分", GOAL, RULES, _BASELINE, '    return affinity if event == "friendly" else None'),
    _m("放宽 R4 客气按报的档算", GOAL, RULES, '        tier = "small"\n    elif event == "repair" and tier == "large":',
       '        pass\n    elif event == "repair" and tier == "large":'),
    _m("放宽 R5 不核对亲近条件编号", GOAL, RULES,
       '        if type(met_index) is not int or not 0 <= met_index < n:', '        if False:'),
    _m("放宽 R6 正向大档不设门槛", GOAL, RULES,
       '    elif event == "met_condition" and tier == "large" and state.nonneg_streak < LARGE_NEEDS_STREAK:',
       '    elif False:'),
    _m("过严 R6 大档一律降为中档", GOAL, RULES, ' and state.nonneg_streak < LARGE_NEEDS_STREAK:', ':'),
    _m("过严 R7 每次冒犯都重记冒犯前的值", UNIT, RULES, _PRE,
       '        pre_offence=affinity if negative else state.pre_offence,'),
    _m("过严 R7 只有 offended 记、trigger 不记（审计 B10）", UNIT, RULES, _PRE,
       '        pre_offence=affinity if event == "offended" and state.pre_offence is None else state.pre_offence,'),
    _m("放宽 R8 道歉修复不设上限", GOAL, RULES, _REPAIR, '    return None'),
    _m("过严 R8 道歉修复不加分", GOAL, RULES, _REPAIR, '    return affinity if event == "repair" else None'),
    _m("放宽 R8 修复的大档不降成中档（审计 A12）", UNIT, RULES,
       '    elif event == "repair" and tier == "large":', '    elif False:'),
    _m("过严 R10 门槛判断时忽略「到过亲近档」（审计 B2）", GOAL, RULES, ' and not state.reached_close and ', ' and '),
    _m("过严 R10 起点就在亲近档的不算到过", GOAL, RULES,
       '        reached_close=state.reached_close or affinity >= CLOSE_FROM,', '        reached_close=state.reached_close,'),
    _m("放宽 R10 所有关系都当作到过亲近档", GOAL, RULES,
       '        reached_close=state.reached_close or affinity >= CLOSE_FROM,', '        reached_close=True,'),
    _m("过严 R17 恢复时不读「到过亲近档」", GOAL, RULES,
       '                   pick("reached_close", is_bool))', '                   False)'),
    _m("过严 R9 冒犯前的值永不清空", UNIT, RULES,
       '    if state.pre_offence is not None and not negative and new >= state.pre_offence:', '    if False:'),
    _m("放宽 R10 亲近门槛 3→2", GOAL, RULES, 'MET_REQUIRED = 3 ', 'MET_REQUIRED = 2 '),
    _m("过严 R10 亲近门槛 3→4", GOAL, RULES, 'MET_REQUIRED = 3 ', 'MET_REQUIRED = 4 '),
    _m("过严 R10 亲近门槛永远不开", GOAL, RULES, 'MET_REQUIRED = 3 ', 'MET_REQUIRED = 999 '),
    _m("放宽 R10/R11 门槛只拦 met_condition", UNIT, RULES, _GATE,
       '    blocked = event == "met_condition" and affinity < CLOSE_FROM and not state.reached_close '
       'and state.met_count < MET_REQUIRED'),
    _m("过严 R12 代码按记仇让小档道歉不算数", GOAL, RULES,
       '    if event == "friendly":                                               # R4：一律小档',
       '    if event == "repair" and psyche.grudge_inertia == "记仇" and tier == "small":\n'
       '        event = "neutral"\n'
       '    if event == "friendly":                                               # R4：一律小档'),
    _m("放宽 R12 评估 prompt 不带这张卡的记仇值（审计 A14）", GOAL, SERVICE,
       '【{psyche.grudge_inertia}】', '【记仇】'),
    _m("放宽 R13 永远用默认亲近条件", GOAL, RULES, _WARMING, '    return list(DEFAULT_WARMING)'),
    _m("过严 R13 有卡上条件仍追加默认条件", GOAL, RULES, _WARMING,
       '    return list(psyche.warming_conditions) + list(DEFAULT_WARMING)'),
    _m("放宽 R14 冲突档不触发", GOAL, RULES, _TIER, '    if False:\n        return "conflict"'),
    _m("放宽 R14 亲近档优先于冲突档", GOAL, RULES,
       _TIER + '\n    return "close" if affinity >= CLOSE_FROM else "normal"',
       '    if affinity >= CLOSE_FROM:\n        return "close"\n'
       '    return "conflict" if state.last_event in NEGATIVE else "normal"'),
    _m("过严 R14 冲突档不退出", GOAL, RULES, '        last_event=event,',
       '        last_event=event if negative else (state.last_event or event),'),
    _m("过严 R14 闲聊也触发冲突档", GOAL, RULES, _TIER,
       '    if state.last_event in NEGATIVE or state.last_event == "neutral":\n        return "conflict"'),
    _m("过严 R15 有三档做法仍用通用语气", GOAL, SCHEMA, _COMPLETE, '        return False'),
    _m("放宽 R15 三档没填全也用", GOAL, SCHEMA, _COMPLETE,
       '        return bool(self.close or self.normal or self.conflict)'),
    _m("过严 R15 有分面仍用写死文案", GOAL, SCHEMA,
       '        return [f.behavior for f in self.agreeableness_facets if f.behavior]', '        return []'),
    _m("放宽 R16 未知事件不拒绝", UNIT, RULES,
       '    if event not in EVENTS:\n        raise ValueError(f"未知的 affinity_event：{event!r}")',
       '    if event not in EVENTS:\n        event = "neutral"'),
    _m("过严 R17 规则状态不落库", GOAL, SERVICE, _PACKED, '            "relation": {},'),
    _m("过严 R17 口头禅保存时不带规则状态（审计 B1）", CATCH, SERVICE, _PACKED, '            "relation": {},'),
    _m("放宽 R17 恢复时不读规则状态", GOAL, SERVICE,
       '        self.relation = RelationState.from_dict(_parsed.get("relation") if _parsed else None)',
       '        self.relation = RelationState()'),
    _m("过严 R17 脏的事件值让恢复崩掉（审计 I1）", UNIT, RULES,
       '        is_event = lambda v: isinstance(v, str) and v in EVENTS', '        is_event = lambda v: v in EVENTS'),
    _m("放宽 R18 默认条件卡不设单轮上限", GOAL, RULES, _LEGACY, '    return None'),
    _m("放宽 R18 同上，换服务层用例来打（审计 T1）", CLAMP, RULES, _LEGACY, '    return None'),
    _m("过严 R18 所有卡都套旧的单轮上限", GOAL, RULES, _LEGACY, '    return affinity + LEGACY_UP_MAX'),
    _m("放宽 F12 「性格特征」仍填 values", GOAL, SERVICE,
       "性格特征：{', '.join(_traits[:3])}", "性格特征：{', '.join(_values[:3])}"),
    _m("放宽 协议 读回答时读回旧字段", GOAL, PROTOCOL, '"delta": data.get(FIELD_DELTA)', '"delta": data.get("affinity")'),
    _m("过严 协议 评估 prompt 不带亲近条件", GOAL, PROTOCOL,
       '    conditions = "".join(f"  {i}. {c}\\n" for i, c in enumerate(warming_conditions(psyche)))',
       '    conditions = ""'),
    # 2026-10-08 收尾（复核 P1、`reason` 改现算）：两条新变异。
    _m("过严 R10 起点正好 73 不算到过", UNIT, RULES,
       '        reached_close=state.reached_close or affinity >= CLOSE_FROM,',
       '        reached_close=state.reached_close or affinity > CLOSE_FROM,'),
    _m("放宽 纯文本 reason 不再落到 inner_voice", CATCH, SERVICE,
       '                self.inner_voice = _reason_raw', '                self.inner_voice = ""'),
]

if __name__ == "__main__":
    lo, hi = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) == 3 else (0, len(MUTANTS))
    sys.exit(framework.run_oneoff(MUTANTS[lo:hi], targets=TARGETS))
