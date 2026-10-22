# -*- coding: utf-8 -*-
"""目标检查：弧线阶段新增 `key_moments`，重定位只按第一件找起点（spec `docs/specs/arc-key-moments.md`）。

G1 发给模型的 G6 结构 / 维度 L 规则要求单独写关键事件；
G2 判断提示词给出「第一件」并查「本人做出或亲身经历」，不再「取最后发生的那一件」；
G3 该判定全仓只在一处定义，规则与提示词都由它生成；
G4 key_moments 留在草稿，不进卡、不进存库 JSON；
G5 锁「只按第一件」：第一件只在第 4 章、第二件只在更早的第 2 章，起点必须落在第 4 章；
key_moments 为空或不是列表时不重定位。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core import phase_locate
from core.card_draft import CardDraft, card_from_draft, draft_schema
from core.distiller import Distiller, format_prompt_after
from core.retro_regions import NOT_DETECTED, RetroRegions

KEY_MOMENT_START = getattr(phase_locate, "KEY_MOMENT_START", None)


def test_g1_g6_schema_and_arc_rule_carry_key_moments():
    phase = draft_schema("G6")["$defs"]["DraftPhase"]
    assert phase["properties"]["key_moments"]["type"] == "array"
    assert "key_moments" in phase.get("required", [])
    arc = format_prompt_after("G6")
    assert "key_moments" in arc
    assert KEY_MOMENT_START in arc


def test_g2_judge_prompt_targets_first_key_moment_and_checks_subject():
    phase = {"label": "认清心意",
             "state": "揽月台见她受伤后彻底认清心意，从此甘愿被她扇巴掌、被她拿捏",
             "key_moments": ["他在揽月台见她受伤"]}
    prompt = phase_locate.judge_prompt("谢凛羽", phase, 424, ["她扇了别人一巴掌。", "他在揽月台见她受伤。"])
    assert "他在揽月台见她受伤" in prompt
    assert "「谢凛羽」本人做出或亲身经历" in prompt
    assert "取最后发生的那一件" not in prompt


def test_g3_start_rule_defined_once_and_used_by_both():
    assert KEY_MOMENT_START, "「标志阶段开始的事」的常量缺失"
    assert KEY_MOMENT_START in format_prompt_after("G6")
    assert KEY_MOMENT_START in phase_locate.judge_prompt(
        "他", {"label": "l", "state": "s", "key_moments": ["m"]}, 1, ["m。"])
    root = Path(__file__).resolve().parents[1]
    skip = {".git", ".claude", "node_modules", ".venv", "__pycache__"}
    hits = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if set(rel.parts) & skip:
            continue
        if KEY_MOMENT_START in path.read_text(encoding="utf-8"):
            hits.append(rel.as_posix())
    hits.sort()
    assert hits == ["core/phase_locate.py"], hits


def test_g4_key_moments_stay_out_of_the_card():
    draft = {"name": "x", "character_arc": {"axis": "a", "phases": [
        {"label": "l1", "state": "s1", "anchor": "", "key_moments": []},
        {"label": "l2", "state": "s2", "anchor": "", "key_moments": ["他在揽月台见她受伤"]},
    ]}}
    parsed = CardDraft.model_validate(draft)
    assert parsed.character_arc.phases[1].key_moments == ["他在揽月台见她受伤"]
    card = card_from_draft(draft, "", retro=NOT_DETECTED)
    dump = card.model_dump()
    assert "key_moments" not in json.dumps(dump, ensure_ascii=False)
    assert set(dump["character_arc"]["phases"][0]) == {
        "behaviors", "boundary_examples", "label", "overlay", "start", "state"}


_SRC = ("第1章\n谢凛羽初入宗门。\n"
        "第2章\n他在揽月台见她受伤。\n"
        "第3章\n他常想起那日的事。\n"
        "第4章\n她在雨夜递来一盏灯。她扇了别人一巴掌。\n"
        "第5章\n此后他依旧守在原地。\n")
_RETRO = RetroRegions("marked", ())
_PHASE = {"label": "认清心意",
          "state": "揽月台见她受伤后彻底认清心意，从此甘愿被她扇巴掌、被她拿捏",
          "anchor": "", "key_moments": ["她在雨夜递来一盏灯", "他在揽月台见她受伤"]}


class _EventJudge:
    """按提示词里要判的那件事作答：旧提示词（取最后一件）→ 扇巴掌；新提示词 → 第一件。"""

    last_usage = None
    model = "m"

    def __init__(self):
        self.asked = []

    def chat(self, system, messages, max_tokens=None, **kw):
        user = messages[-1]["content"]
        n = int(re.search(r"### 第(\d+)章", user).group(1))
        m = re.search(re.escape(KEY_MOMENT_START) + r"：(.+)", user) if KEY_MOMENT_START else None
        if m:
            target = m.group(1)
        elif "取最后发生的那一件" in user:
            target = "扇巴掌"
        else:
            target = ""
        self.asked.append((n, target))
        if "揽月台" in target:
            happens = n == 2
            key = "揽月台"
        elif "雨夜" in target:
            happens = n == 4
            key = "雨夜"
        elif "扇巴掌" in target:
            happens = n == 4
            key = "一巴掌"
        else:
            happens, key = False, ""
        if not happens:
            return json.dumps({"happens": False, "sent": "", "why": "否"})
        sents = re.findall(r"S(\d+)：(.*)", user)
        sid = next(i for i, s in sents if key in s)
        return json.dumps({"happens": True, "sent": f"S{sid}", "why": "是"})


def _draft(phase: dict) -> dict:
    return {"name": "谢凛羽", "character_arc": {"axis": "从冷漠到甘愿", "phases": [
        {"label": "初入宗门", "state": "初入宗门时心性冷淡", "anchor": ""},
        phase]}}


def _chapter_of(pos: int) -> int:
    from core.quotes import normalize
    heads = [(int(m.group(1)), len(normalize(_SRC[:m.start()])))
             for m in re.finditer(r"(?m)^第(\d+)章", _SRC)]
    return max(n for n, p in heads if p <= pos)


def test_g5_relocation_uses_the_first_key_moment():
    data = _draft(dict(_PHASE))
    Distiller(llm=_EventJudge(), config_path=None).locate_phase_starts(
        data, _SRC, "谢凛羽", _RETRO)
    start = card_from_draft(data, _SRC, retro=_RETRO).character_arc.phases[1].start
    assert start is not None
    assert _chapter_of(start) == 4


def test_g5_phase_without_key_moments_is_not_relocated():
    phase = dict(_PHASE)
    phase["key_moments"] = []
    data = _draft(phase)
    Distiller(llm=_EventJudge(), config_path=None).locate_phase_starts(
        data, _SRC, "谢凛羽", _RETRO)
    assert card_from_draft(data, _SRC, retro=_RETRO).character_arc.phases[1].start is None
    assert data["character_arc"]["phases"][1]["anchor"] == ""
