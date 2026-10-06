# -*- coding: utf-8 -*-
"""arc-phase-fields §6.1：单元测试 U1–U19（先红后绿）。

本段把「所有进 prompt 的字段」按阶段投影：经历类只含阶段 1..k，状态类是阶段 k 那个人的
样子。〔arc-phase-fields §6.1〕
"""
from __future__ import annotations

import typing

import pytest
from pydantic import BaseModel

from core.schema import ArcPhase, CharacterArc, CharacterCard, PhaseAttitude, Relationship


# ── 造卡助手 ────────────────────────────────────────────────────────────

def make_card(n_phases: int = 3, *, starts: list[int] | None = None,
              fingerprint: str = "fp", **overlay_per_phase) -> CharacterCard:
    """造一张有 n 个阶段的卡；overlay_per_phase 形如 phases=[{...}, ...]。"""
    phases = [ArcPhase(label=f"P{i + 1}", state=f"状态{i + 1}") for i in range(n_phases)]
    if starts is not None:
        for p, s in zip(phases, starts):
            p.start = s
    return CharacterCard(
        name="甲", identity="身份", background="背景",
        character_arc=CharacterArc(axis="从A到B", phases=phases, source_fingerprint=fingerprint),
    )


def set_overlay(card: CharacterCard, idx: int, path: str, value) -> None:
    card.character_arc.phases[idx].overlay[path] = value


# ── U1 登记表 = 叶子全集 ──────────────────────────────────────────────────

def _leaves(model: type[BaseModel], prefix: str = "") -> list[str]:
    """展开 BaseModel 子模型、不展开 list[BaseModel]（与附录 B 同口径）。"""
    out: list[str] = []
    for name, f in model.model_fields.items():
        ann = f.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out += _leaves(ann, f"{prefix}{name}.")
        else:
            out.append(f"{prefix}{name}")
    return out


def test_registry_equals_leaf_set():
    """U1：登记表 = `CharacterCard` 叶子全集（多、少都红）。"""
    from core.card_layers import REGISTRY

    assert set(REGISTRY) == set(_leaves(CharacterCard))
    assert len(REGISTRY) == 37


def test_registry_every_entry_has_layer_and_kind():
    """U1：每条登记都带类别与形态，且取值在允许集合内。"""
    from core.card_layers import REGISTRY, LAYERS, KINDS

    for path, spec in REGISTRY.items():
        assert spec.layer in LAYERS, path
        assert spec.kind in KINDS, path


# ── U2/U3/U4/U5/U6 投影规则 ───────────────────────────────────────────────

def test_state_list_is_phase_k_first_then_top():
    """U2：状态（列表）= 阶段 k 特有在前 + 全程在后，不累加、不取全书（B3 顺序契约）。"""
    from core.arc_view import project_card

    c = make_card(3)
    c.personality_traits = ["全程"]
    set_overlay(c, 0, "personality_traits", ["一"])
    set_overlay(c, 1, "personality_traits", ["二"])
    set_overlay(c, 2, "personality_traits", ["三"])

    assert project_card(c, 2)[0].personality_traits == ["二", "全程"]
    assert project_card(c, 3)[0].personality_traits == ["三", "全程"]


def test_state_scalar_falls_back_to_top():
    """U3：状态（单值）阶段 k 有值用之，否则回落顶层。"""
    from core.arc_view import project_card

    c = make_card(3)
    c.decision_style = "顶层"
    set_overlay(c, 2, "decision_style", "阶段三")

    assert project_card(c, 2)[0].decision_style == "顶层"
    assert project_card(c, 3)[0].decision_style == "阶段三"


def test_nested_path_projection():
    """U4：嵌套路径（speaking_style.catchphrases / psyche.triggers）按登记表投影。"""
    from core.arc_view import project_card

    c = make_card(2)
    c.speaking_style.catchphrases = ["顶层口癖"]
    c.psyche.triggers = ["顶层雷"]
    set_overlay(c, 1, "speaking_style.catchphrases", ["阶段二口癖"])
    set_overlay(c, 1, "psyche.triggers", ["阶段二雷"])

    proj = project_card(c, 2)[0]
    assert proj.speaking_style.catchphrases == ["阶段二口癖", "顶层口癖"]
    assert proj.psyche.triggers == ["阶段二雷", "顶层雷"]


def test_experience_takes_1_to_k():
    """U5：经历（列表）= 顶层 + 阶段 1..k；k 之后的不出现。"""
    from core.arc_view import project_card

    c = make_card(3)
    c.key_memories = ["顶层记忆"]
    set_overlay(c, 0, "key_memories", ["一"])
    set_overlay(c, 1, "key_memories", ["二"])
    set_overlay(c, 2, "key_memories", ["三"])

    assert project_card(c, 1)[0].key_memories == ["顶层记忆", "一"]
    assert project_card(c, 2)[0].key_memories == ["顶层记忆", "一", "二"]
    assert project_card(c, 3)[0].key_memories == ["顶层记忆", "一", "二", "三"]


def test_experience_scalar_joined_by_semicolon():
    """U5：经历（单值）= 顶层 + 1..k 用「；」连接。"""
    from core.arc_view import project_card

    c = make_card(2)
    c.cognitive.knowledge_scope = "顶层"
    set_overlay(c, 0, "cognitive.knowledge_scope", "一")
    set_overlay(c, 1, "cognitive.knowledge_scope", "二")

    assert project_card(c, 1)[0].cognitive.knowledge_scope == "顶层；一"
    assert project_card(c, 2)[0].cognitive.knowledge_scope == "顶层；一；二"


def test_stable_fields_untouched_and_original_not_mutated():
    """U6：稳定字段原样；投影不改原卡。"""
    from core.arc_view import project_card

    c = make_card(2)
    c.values = ["顶层价值"]
    set_overlay(c, 1, "values", ["二"])
    c.psyche.openness = 4

    proj = project_card(c, 3)[0]
    assert proj.psyche.openness == 4
    assert proj.name == "甲" and proj.identity == "身份" and proj.background == "背景"
    # 原卡不被改
    assert c.values == ["顶层价值"]
    assert c.character_arc.phases[1].overlay["values"] == ["二"]


# ── U9 关系 note 随阶段 ──────────────────────────────────────────────────

def test_relationship_note_follows_phase():
    """U9：关系口径 `note` 随阶段（取 ≤k 最新一条）。"""
    from core.arc_view import project_card

    c = make_card(2)
    c.relationships = [Relationship(
        target="乙", relation="同窗", attitude="平淡", note="普通同学",
        phase_attitudes=[PhaseAttitude(phase=1, attitude="平淡", note="普通同学"),
                         PhaseAttitude(phase=2, attitude="亲密", note="生死之交")],
    )]

    assert project_card(c, 1)[0].relationships[0].note == "普通同学"
    assert project_card(c, 2)[0].relationships[0].note == "生死之交"
    assert project_card(c, 2)[0].relationships[0].attitude == "亲密"


# ── U10 selectable 与 valid_phase ────────────────────────────────────────

def test_selectable_and_valid_phase():
    """U10：起点齐全 + 指纹（`has_positions`）→ 可选；否则 valid_phase 恒 None。

    `selectable` 不再是模型上的字段（B4：派生值不落库），出卡时才现算 —— 判定口径仍
    只有 `CharacterArc.has_positions` 一处。
    """
    from core.arc_view import valid_phase

    good = make_card(3, starts=[0, 10, 20], fingerprint="fp")
    assert good.character_arc.has_positions() is True
    assert valid_phase(good, 2) == 2
    assert valid_phase(good, None) is None

    no_start = make_card(3, fingerprint="fp")           # 起点缺失
    assert no_start.character_arc.has_positions() is False
    assert valid_phase(no_start, 2) is None

    no_fp = make_card(3, starts=[0, 10, 20], fingerprint="")  # 无指纹
    assert no_fp.character_arc.has_positions() is False
    assert valid_phase(no_fp, 2) is None


# ── U11 ①格式卡迁移 ──────────────────────────────────────────────────────

def test_legacy_phase_fields_migrate_to_overlay():
    """U11：①格式（`phases[].memories` / `dialogue_examples`）加载时搬进 overlay，
    且 C14 不再重复（`dispatch([[2,3]],3)` 的重复挂载形态）。"""
    legacy = {
        "name": "甲",
        "character_arc": {"axis": "从A到B", "phases": [
            {"state": "一", "memories": ["M1"], "dialogue_examples": ["D1"]},
            {"state": "二", "memories": ["M2"], "dialogue_examples": ["D2"]},
            {"state": "三", "memories": ["M3"], "dialogue_examples": ["D3"]},
        ]},
    }
    card = CharacterCard.model_validate(legacy)
    p2, p3 = card.character_arc.phases[1], card.character_arc.phases[2]
    assert p2.overlay.get("key_memories") == ["M2"]
    assert p3.overlay.get("key_memories") == ["M3"]
    assert p2.overlay.get("dialogue_examples") == ["D2"]

    from core.arc_view import project_card
    assert project_card(card, 3)[0].key_memories == ["M1", "M2", "M3"]   # 不重复


def test_behaviors_not_migrated():
    """U11：`behaviors` 不动（留给②）。"""
    legacy = {"name": "甲", "character_arc": {"phases": [
        {"state": "一", "behaviors": [{"situation": "s", "behavior": "b"}]},
    ]}}
    card = CharacterCard.model_validate(legacy)
    assert card.character_arc.phases[0].behaviors[0].behavior == "b"
    assert "situation_behaviors" not in card.character_arc.phases[0].overlay


# ── U12 overlay 校验 ─────────────────────────────────────────────────────

def test_overlay_rejects_unregistered_key():
    """U12：overlay 键必须 ∈ 登记表 state/experience 路径。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ArcPhase(state="一", overlay={"nope": ["x"]})


def test_overlay_rejects_type_mismatch():
    """U12：overlay 值类型须与登记 kind 一致。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ArcPhase(state="一", overlay={"personality_traits": "不是列表"})
    with pytest.raises(ValidationError):
        ArcPhase(state="一", overlay={"decision_style": ["不是单值"]})


# ── U7/U8 分发按类别 ────────────────────────────────────────────────────

def test_dispatch_state_hangs_every_valid_phase():
    """U7：state → 每个最终阶段；覆盖全部 → 顶层。"""
    from core.card_draft import dispatch

    top, by_phase = dispatch([[2, 3]], 3, layer="state")
    assert top == []
    assert by_phase == [[], [0], [0]]
    assert dispatch([[1, 2, 3]], 3, layer="state") == ([0], [[], [], []])


def test_dispatch_experience_hangs_earliest_only():
    """U7：experience → 只最早阶段；从阶段 1 起覆盖全部 → 顶层。"""
    from core.card_draft import dispatch

    top, by_phase = dispatch([[2, 3]], 3, layer="experience")
    assert top == []
    assert by_phase == [[], [0], []]                 # 只挂阶段 2
    assert dispatch([[1, 2, 3]], 3, layer="experience") == ([0], [[], [], []])
    assert dispatch([[1, 3]], 3, layer="experience") == ([], [[0], [], []])


def test_dispatch_scalar_one_per_slot():
    """U8：单值字段一个格子只留第一条 —— 同一阶段两条，第二条丢弃。"""
    from core.card_draft import dispatch

    top, by_phase = dispatch([[2], [2]], 3, layer="state", kind="scalar")
    assert top == []
    assert by_phase == [[], [0], []]


def test_dispatch_scalar_multi_phase_fills_each_slot():
    """U8：单值字段标注多个阶段 → 每个阶段各得该值（不是只取第一个阶段）。"""
    from core.card_draft import dispatch

    top, by_phase = dispatch([[2, 3]], 3, layer="state", kind="scalar")
    assert top == []
    assert by_phase == [[], [0], [0]]


def test_dispatch_scalar_all_phases_top_takes_first():
    """U8：单值字段两条都覆盖全部 → 顶层只留第一条。"""
    from core.card_draft import dispatch

    top, by_phase = dispatch([[1, 2, 3], [1, 2, 3]], 3, kind="scalar")
    assert top == [0]
    assert by_phase == [[], [], []]


def test_dispatch_count_zero():
    """U8：无阶段的卡全部落顶层；单值字段顶层格子同样只留第一条。"""
    from core.card_draft import dispatch

    assert dispatch([[1, 2], [1, 2]], 0, kind="list") == ([0, 1], [])
    assert dispatch([[1, 2], [1, 2]], 0, kind="scalar") == ([0], [])


def test_scalar_same_phase_second_dropped_not_promoted():
    """U8：单值字段同一阶段两条 —— 第二条丢弃，**不进顶层**（B1，分发只有一套）。"""
    from core.card_draft import card_from_draft

    src = "开头甲甲甲。中间乙乙乙。"
    data = {
        "name": "甲",
        "character_arc": {"phases": [
            {"label": "一", "state": "s1", "anchor": ""},
            {"label": "二", "state": "s2", "anchor": "中间乙乙乙"}]},
        "decision_style": [
            {"value": "早期谨慎", "occurrences": [{"phase": 1, "quote": "开头甲甲甲"}]},
            {"value": "重复条目", "occurrences": [{"phase": 1, "quote": "开头甲甲甲"}]}],
    }
    card = card_from_draft(data, src)
    assert card.decision_style == ""
    assert card.character_arc.phases[0].overlay.get("decision_style") == "早期谨慎"


def test_scalar_multi_phase_lands_in_each_phase():
    """U8：单值字段一条标 [2,3] → 阶段 2、3 各有该值（B1）。"""
    from core.card_draft import card_from_draft

    src = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
    data = {
        "name": "甲",
        "character_arc": {"phases": [
            {"label": "一", "state": "s1", "anchor": ""},
            {"label": "二", "state": "s2", "anchor": "中间乙乙乙"},
            {"label": "三", "state": "s3", "anchor": "结尾丙丙丙"}]},
        "decision_style": [{"value": "中期起果断", "occurrences": [
            {"phase": 2, "quote": "中间乙乙乙"},
            {"phase": 3, "quote": "结尾丙丙丙"}]}],
    }
    card = card_from_draft(data, src)
    assert card.decision_style == ""
    assert card.character_arc.phases[1].overlay.get("decision_style") == "中期起果断"
    assert card.character_arc.phases[2].overlay.get("decision_style") == "中期起果断"


# ── U7b 草稿形态由登记表派生（§4.2）─────────────────────────────────────

def _draft_ann(path: str):
    from core.card_draft import CardDraft

    ann = CardDraft
    for part in path.split("."):
        ann = ann.model_fields[part].annotation
    return ann


def test_draft_shape_derived_from_registry():
    """U7b：状态/经历类字段的草稿形态是 `list[DraftTimed]`（顶层与嵌套一视同仁），
    做法/记忆各自的草稿类不动，稳定类原样 —— 全部由登记表派生，不逐字段手写。"""
    from core.card_draft import CardDraft, DraftBehavior, DraftMemory, DraftTimed
    from core.card_layers import REGISTRY

    assert _draft_ann("name") is str
    assert _draft_ann("speaking_style.vocabulary_level") is str       # 稳定类不动
    assert _draft_ann("situation_behaviors") == list[DraftBehavior]   # 各自的草稿类
    assert _draft_ann("key_memories") == list[DraftMemory]

    derived = [p for p, s in REGISTRY.items()
               if s.layer in ("state", "experience")
               and p not in ("situation_behaviors", "key_memories")]
    assert derived, "登记表里没有状态/经历类字段，判据失去意义"
    for path in derived:
        assert _draft_ann(path) == list[DraftTimed], path
    assert CardDraft.model_json_schema()["title"] == "CharacterCard"


def test_timed_draft_fields_convert_to_overlay_and_top():
    """U7b：`list[DraftTimed]` 按类别落卡 —— 覆盖全部 → 顶层；标部分阶段 → 该阶段 overlay。"""
    from core.card_draft import card_from_draft

    src = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
    data = {
        "name": "甲",
        "character_arc": {"phases": [
            {"label": "一", "state": "s1", "anchor": ""},
            {"label": "二", "state": "s2", "anchor": "中间乙乙乙"}]},
        "personality_traits": [
            {"value": "全程都这样",
             "occurrences": [{"phase": 1, "quote": "开头甲甲甲"},
                             {"phase": 2, "quote": "中间乙乙乙"}]},
            {"value": "后来才这样", "occurrences": [{"phase": 2, "quote": "中间乙乙乙"}]}],
        "decision_style": [{"value": "一辈子都谨慎",
                            "occurrences": [{"phase": 1, "quote": "开头甲甲甲"},
                                            {"phase": 2, "quote": "中间乙乙乙"}]}],
    }
    card = card_from_draft(data, src)
    assert card.personality_traits == ["全程都这样"]
    assert card.decision_style == "一辈子都谨慎"
    assert card.character_arc.phases[0].overlay.get("personality_traits") is None
    assert card.character_arc.phases[1].overlay.get("personality_traits") == ["后来才这样"]


# ── U13 依赖组推导 ──────────────────────────────────────────────────────

def test_phase_dependent_groups_derived():
    """U13：依赖组由登记表推导 —— G2、G3、G4 依赖；G1、G5 不依赖。"""
    from core.distiller import PHASE_DEPENDENT_GROUPS

    assert PHASE_DEPENDENT_GROUPS == {"G2", "G3", "G4"}


# ── U14 审核覆盖 overlay ────────────────────────────────────────────────

def test_review_covers_overlay():
    """U14：审核遍历必须覆盖 overlay 里的内容（不只顶层字段）。"""
    from core.moderation.auto_review import _flatten_card

    card = make_card(2)
    set_overlay(card, 0, "key_memories", ["阶段一才有的记忆内容长度超过二十个字符"])
    flat = _flatten_card(card.model_dump())
    assert "阶段一才有的记忆内容长度超过二十个字符" in flat


# ── U15 市场 @ 回复与导出读最后阶段 ──────────────────────────────────────

def test_export_reads_last_phase_and_lists_overlay():
    """U15：导出用最后阶段投影；阶段特有内容按阶段列出。"""
    from core.export import to_tavern_json

    c = make_card(3)
    c.personality_traits = ["全程"]
    set_overlay(c, 2, "personality_traits", ["三"])
    set_overlay(c, 1, "key_memories", ["阶段二记忆"])
    data = to_tavern_json(c)
    blob = data["data"]["description"] + data["data"]["personality"]
    assert "三" in blob
    assert "阶段二记忆" in blob


# ── U21 B6：阶段内容只出现一次，且落在对应阶段的段落里 ──────────────────────

def _sections(text: str) -> dict[str, str]:
    """按阶段表头把导出正文切段；全程段（无表头）键为 ``""``。"""
    out: dict[str, list[str]] = {"": []}
    cur = ""
    for line in text.splitlines():
        if line.startswith("阶段 "):
            cur = line
            out[cur] = []
        else:
            out.setdefault(cur, []).append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def test_export_phase_content_appears_once_in_its_section():
    """B6：阶段记忆 / 阶段性格各只出现一次，且在**对应阶段的段落**里。"""
    from core.arc_view import phase_header
    from core.export import to_tavern_json

    c = make_card(3)
    c.personality_traits = ["全程性格"]
    c.key_memories = ["全程记忆"]
    set_overlay(c, 0, "personality_traits", ["早期才有的性格"])
    set_overlay(c, 1, "key_memories", ["中期才有的记忆"])

    text = to_tavern_json(c)["data"]["personality"]
    assert text.count("早期才有的性格") == 1, "阶段性格重复出现或消失"
    assert text.count("中期才有的记忆") == 1, "阶段记忆重复出现或消失"

    labels = [p.label for p in c.character_arc.phases]
    secs = _sections(text)
    assert "早期才有的性格" in secs[phase_header(1, labels[0])]
    assert "中期才有的记忆" in secs[phase_header(2, labels[1])]
    assert "早期才有的性格" not in secs[""], "阶段内容漏进了全程段"
    assert "中期才有的记忆" not in secs[""], "阶段内容漏进了全程段"


# ── U22 B5：导出正文的字段名取自登记表 ────────────────────────────────────

def test_export_field_names_come_from_registry(monkeypatch):
    """B5：正文里的字段名是登记表 ``label`` —— 改登记表即改导出（不是各自写死）。"""
    import core.card_layers as cl
    import core.arc_view as av
    from core.export import to_tavern_json

    patched = dict(cl.REGISTRY)
    patched["key_memories"] = cl.FieldSpec("experience", "list", "独家记忆名")
    monkeypatch.setattr(cl, "REGISTRY", patched)
    monkeypatch.setattr(av, "REGISTRY", patched)          # arc_view 顶层已绑定自己那份

    c = make_card(2)
    c.key_memories = ["一段记忆"]
    text = to_tavern_json(c)["data"]["personality"]
    assert "独家记忆名：一段记忆" in text
    assert "关键记忆" not in text, "导出还写着旧字段名"


# ── U16/U17 关系分批 ────────────────────────────────────────────────────

def test_relationship_batch_splits_and_merges():
    """U16：23 人 → 3 批，每批 ≤10，全部汇总、顺序稳定。"""
    from core.relationship_batch import batch_relationships, REL_BATCH_SIZE

    assert REL_BATCH_SIZE == 10
    targets = [f"人{i}" for i in range(23)]
    calls: list[list[str]] = []

    def fake_call(system: str, user: str, batch: list[str]):
        calls.append(list(batch))
        return [{"target": t, "relation": "友", "note": ""} for t in batch]

    out = batch_relationships(targets, ["阶段一", "阶段二"],
                              stream_call=fake_call, prefix="前缀", name="主角")
    assert [len(c) for c in calls] == [10, 10, 3]
    assert [r["target"] for r in out] == targets


def test_relationship_batch_fail_all_on_any_batch():
    """U16：任一批失败 → 整步失败（不静默丢人）。"""
    from core.relationship_batch import batch_relationships, RelationshipBatchError

    def failing(system, user, batch):
        raise RuntimeError("batch down")

    with pytest.raises(RelationshipBatchError):
        batch_relationships([f"人{i}" for i in range(12)], ["阶段一"],
                            stream_call=failing, prefix="前缀", name="主角")


def test_relationship_batch_prefix_passed_verbatim():
    """U16：每批用的前缀与主调用前缀逐字相同。"""
    from core.relationship_batch import batch_relationships

    seen: list[str] = []

    def capture(system, user, batch):
        seen.append(system)
        return [{"target": t, "relation": "友"} for t in batch]

    batch_relationships(["甲", "乙"], ["阶段一"], stream_call=capture, prefix="SHARED-PREFIX", name="主角")
    assert all("SHARED-PREFIX" in p for p in seen)
    assert len(seen) == 1, "没人缺失就不该补跑（前缀判据只需看首批）"


# ── U18 类型隔离 ────────────────────────────────────────────────────────

def test_projected_card_type_only_from_project_card():
    """U18：拼角色扮演 prompt 的入口只收 `ProjectedCard`；收原卡抛 TypeError。"""
    from core.arc_view import ProjectedCard, project_card
    from core.context_engine import ContextEngine

    c = make_card(2)
    proj = project_card(c, 2)[0]
    assert isinstance(proj, ProjectedCard)

    with pytest.raises(TypeError):
        ContextEngine(c, rag=None, storage=None)      # 原卡 → 抛


# ── U20 位置核对的上下文只建一次（效率 #1）─────────────────────────────

def test_anchors_built_once_per_card(monkeypatch):
    """U20：一次 `card_from_draft` 里整本原文只规范化一次 —— 位置核对的上下文只建一次。

    修复前每个字段各归一化一遍整本书（做法 / 记忆 / 关系 + 每个 state、experience 字段
    各一次），书越大重复成本越高。计数口径是 `core.phase_anchoring` 自己那个 `normalize`
    绑定 —— 引用侧 `core.quotes.normalize` 只归一化短摘录，不在此列。
    """
    import core.phase_anchoring as pa
    from core.card_draft import card_from_draft

    calls = 0

    def counting(s):
        nonlocal calls
        calls += 1
        return real(s)

    real = pa.normalize
    monkeypatch.setattr(pa, "normalize", counting)

    src = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
    data = {
        "name": "甲",
        "character_arc": {"phases": [
            {"label": "一", "state": "s1", "anchor": ""},
            {"label": "二", "state": "s2", "anchor": "中间乙乙乙"}]},
        "personality_traits": [{"value": "全程", "occurrences": [
            {"phase": 1, "quote": "开头甲甲甲"}, {"phase": 2, "quote": "中间乙乙乙"}]}],
        "key_memories": [{"memory": "记忆", "occurrences": [
            {"phase": 1, "quote": "开头甲甲甲"}]}],
        "situation_behaviors": [{"situation": "情境", "behavior": "做法",
                                 "occurrences": [{"phase": 2, "quote": "中间乙乙乙"}]}],
        "relationships": [{"target": "乙", "relation": "熟人", "attitudes": [
            {"phase": 2, "attitude": "熟", "quote": "中间乙乙乙"}]}],
    }
    card = card_from_draft(data, src)
    assert card.name == "甲"          # 先证位置核对确实跑过（不是提前 return）
    assert calls == 1


# ── U22/U23 提示词 = 共享前缀 + 本步指令（B2 提示词、B7）────────────────

def test_relationship_batch_prefix_is_shared_step_free():
    """U22：关系分批的系统提示以 `book_prefix` 开头，且不含主调用维度 F 的「只出名单」。

    分批这一步要的是**关系详情**，主调用的维度 F 却写着「关系的类型、态度与阶段变化由
    后续单独生成，这里不要写」——分批把整段主提示当前缀，等于把「不要写」带进了「给我写」。
    前缀只能是共享的、不含本步指令的那一段（`book_prefix`：正文）。
    """
    import json
    from core.distiller import Distiller, book_prefix

    systems: list[str] = []

    class _LLM:
        model = "stub-rel"
        last_usage = None

        def chat_stream_long(self, system, messages, max_tokens=None, **kw):
            systems.append(system)
            yield json.dumps([{
                "target": "乙", "relation": "同窗", "attitude": "亲近", "note": "老同学",
                "attitudes": [{"phase": 1, "attitude": "亲近", "quote": "开头甲甲甲",
                               "note": "老同学"}],
            }], ensure_ascii=False)
            return {"prompt_tokens": 1, "completion_tokens": 1}

    src = "开头甲甲甲。中间乙乙乙。"
    d = Distiller(llm=_LLM(), config_path=None)
    draft = {"name": "甲", "relationships": [{"target": "乙"}]}
    d.fill_relationships(draft, src, "甲")

    assert systems, "关系分批没调模型（判据失去意义）"
    assert systems[0].startswith(book_prefix(src)), "分批前缀不是共享前缀（缓存命中不上）"
    assert "只出名单" not in systems[0], "分批前缀把维度 F 的「只出名单」带进来了"
    assert draft["relationships"][0]["relation"] == "同窗", "分批结果没写回草稿"


def test_relationship_note_flows_from_draft_to_projection():
    """U23：草稿里按阶段写的 note 经 `card_from_draft` 落到 `phase_attitudes`，投影按阶段取。

    注入口径 `note` 是「喂给聊天模型的固定立场」，必须随阶段变 —— 草稿侧按阶段写、
    转卡时落进 `phase_attitudes[i].note`、`project_card` 取 ≤k 最新一条。
    """
    from core.arc_view import project_card
    from core.card_draft import card_from_draft

    src = "开头甲甲甲。中间乙乙乙。"
    data = {
        "name": "甲",
        "character_arc": {"phases": [
            {"label": "一", "state": "s1", "anchor": ""},
            {"label": "二", "state": "s2", "anchor": "中间乙乙乙"}]},
        "relationships": [{
            "target": "乙", "relation": "同窗",
            "attitudes": [
                {"phase": 1, "attitude": "平淡", "quote": "开头甲甲甲", "note": "普通同学"},
                {"phase": 2, "attitude": "亲密", "quote": "中间乙乙乙", "note": "生死之交"}]}],
    }
    card = card_from_draft(data, src)
    notes = {p.phase: p.note for p in card.relationships[0].phase_attitudes}
    assert notes == {1: "普通同学", 2: "生死之交"}, "草稿的按阶段 note 没落进 phase_attitudes"
    assert project_card(card, 1)[0].relationships[0].note == "普通同学"
    assert project_card(card, 2)[0].relationships[0].note == "生死之交"


# ── U24–U28 关系完整性：缺人补一次、别名算在场、名单外丢弃（B2，Shiyu 定）──

def test_relationship_batch_recovers_missing_once():
    """U24：模型漏掉名单里的人 → 只对缺的那些补跑一次，补齐后顺序照名单。

    判据是「补跑且只补跑缺的人、且只一次」：把缺的人也算进补跑批，或补跑整批，
    都在 `calls` 上看得见。
    """
    from core.relationship_batch import batch_relationships

    calls: list[list[str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append(list(batch))
        # 首批漏掉「乙」，补跑那批（只含「乙」）就答得出来。
        return [{"target": t, "relation": "友"}
                for t in batch if t != "乙" or len(calls) > 1]

    out = batch_relationships(["甲", "乙", "丙"], ["阶段一"],
                              stream_call=fake, prefix="P", name="主角")

    assert calls[0] == ["甲", "乙", "丙"], "首批不是全体"
    assert calls[1] == ["乙"], "补跑的不是且只是缺的那个人"
    assert len(calls) == 2, "补跑应恰好一次"
    assert [r["target"] for r in out] == ["甲", "乙", "丙"], "补齐后没按名单顺序"


def test_relationship_batch_missing_after_retry_raises():
    """U25：补跑一次仍缺 → 整步失败，且点名是谁（不静默丢人）。

    裸 `pytest.raises` 会被任何 `RelationshipBatchError` 满足（别的批失败也抛它），
    故用 `match=` 钉住缺的人名。
    """
    from core.relationship_batch import batch_relationships, RelationshipBatchError

    calls: list[list[str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append(list(batch))
        return [{"target": t, "relation": "友"} for t in batch if t != "乙"]

    with pytest.raises(RelationshipBatchError, match="乙"):
        batch_relationships(["甲", "乙"], ["阶段一"], stream_call=fake, prefix="P", name="主角")
    assert len(calls) == 2, "应只补跑一次就失败"


def test_relationship_batch_wrong_name_is_missing_and_extra_dropped():
    """U26：写错名（不在名单里）的条目算缺人（要补跑）、且被丢弃，不混进草稿。

    名单是我们发出的、提示词要求逐字照抄 target，故比对是**逐字字符串**：模型把「乙」
    写成「宝玉」时，对不出「乙」已回 → 「宝玉」那条丢弃、「乙」判缺 → 补跑一次。
    """
    from core.relationship_batch import batch_relationships

    calls: list[list[str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append(list(batch))
        if len(calls) == 1:
            # 首批：甲 回了，乙 被写成了名单外的「宝玉」。
            return [{"target": "甲", "relation": "友"},
                    {"target": "宝玉", "relation": "亲人"}]
        return [{"target": t, "relation": "友"} for t in batch]

    out = batch_relationships(["甲", "乙"], ["阶段一"], stream_call=fake, prefix="P", name="主角")

    assert calls == [["甲", "乙"], ["乙"]], "写错名没算缺人（或补跑的不是缺的那个）"
    assert [r["target"] for r in out] == ["甲", "乙"], "名单外的条目混进来了 / 缺人没补齐"


def test_relationships_batched_goes_through_stream():
    """U28：`Distiller._relationships_batched` 每批真的走 `_collect_stream`（流式）。

    非流式会撞生成轮的 45 s 单次 / 60 s 总墙钟（C20）。这是**行为锁**：原先 MA19 盯的
    是源码里 `_collect_stream(` 的字面（把调用换成非流式也照绿），这里改成看模型适配器
    实际收到的是 `chat_stream_long`（流式入口）而不是 `chat`（非流式入口）。
    """
    import json
    from core.distiller import Distiller

    streamed: list[str] = []
    chatted: list[str] = []

    class _LLM:
        model = "stub-stream"
        last_usage = None

        def chat_stream_long(self, system, messages, max_tokens=None, **kw):
            streamed.append(system)
            yield json.dumps([{"target": "乙", "relation": "同窗"}], ensure_ascii=False)
            return {"prompt_tokens": 1, "completion_tokens": 1}

        def chat(self, system, messages, max_tokens=None, **kw):
            chatted.append(system)
            return "[]"

    d = Distiller(llm=_LLM(), config_path=None)
    draft = {"name": "甲", "relationships": [{"target": "乙"}]}
    d._relationships_batched(draft, prefix="P", character_name="甲", material="")

    assert streamed, "关系分批没走流式（判据失去意义）"
    assert not chatted, "关系分批走了非流式的 chat"
    assert draft["relationships"][0]["relation"] == "同窗", "分批结果没写回草稿"


# ── 本轮审计 R1 / R2（Claude 修）────────────────────────────────────────

def _capture_llm(seen: list):
    """记下每次流式调用的 (system, messages)，答一条合法关系。"""
    import json

    class _LLM:
        model = "stub-rel"
        last_usage = None

        def chat_stream_long(self, system, messages, max_tokens=None, **kw):
            seen.append((system, messages))
            yield json.dumps([{"target": "乙", "relation": "同窗", "attitude": "亲近",
                               "note": "老同学", "attitudes": []}], ensure_ascii=False)
            return {"prompt_tokens": 1, "completion_tokens": 1}

    return _LLM()


def test_r1_batch_user_message_is_the_steps_own_longcontext():
    """R1：一次读完路径的分批，user 消息是关系这一步自己的请求，不是主调用的「生成角色卡」。

    主调用的 user 是「请基于以上全文为「X」生成角色卡」；带进分批就与「只输出关系数组」冲突，
    模型会照它输出整张卡。system 前缀只认正文（缓存），user 换成本步自己的不影响命中。
    """
    from core.distiller import Distiller

    seen: list = []
    d = Distiller(llm=_capture_llm(seen), config_path=None)
    draft = {"name": "甲", "relationships": [{"target": "乙"}]}
    d.fill_relationships(draft, "开头甲甲甲。中间乙乙乙。", "甲")

    assert seen, "关系分批没调模型（判据失去意义）"
    system, messages = seen[0]
    user = messages[-1]["content"]
    assert "角色卡" not in user, f"分批的 user 消息带着主调用的指令：{user}"
    assert "「甲」" in user and "乙" in user, "user 消息没点名主角与本批人物"
    assert "主角是「甲」" in system, "system 里没写主角是谁（正文前缀之后只剩关系规则）"


def test_r1_batch_grouped_path_carries_material_not_main_instruction():
    """R1：分组路径的分批，素材（分析档案）进本步的 user 消息，主调用那句「输出角色卡」不进。"""
    from core.distiller import Distiller, format_prompt_shared

    seen: list = []
    d = Distiller(llm=_capture_llm(seen), config_path=None)
    draft = {"name": "甲", "relationships": [{"target": "乙"}]}
    d._relationships_batched(draft, prefix=format_prompt_shared("甲"),
                             character_name="甲", material="档案正文ABC")

    system, messages = seen[0]
    user = messages[-1]["content"]
    assert "档案正文ABC" in user, "分组路径的分析档案没进分批（模型没有素材可写）"
    assert "角色卡" not in user, f"分批的 user 消息带着主调用的指令：{user}"
    assert system.startswith(format_prompt_shared("甲")), "分组路径前缀不是组共享段"


def _rel_card(phase_attitudes, top_note="全书口径（后期）"):
    from core.schema import CharacterCard
    return CharacterCard.model_validate({
        "name": "甲",
        "character_arc": {"phases": [{"label": f"阶段{i}", "state": f"s{i}"} for i in (1, 2, 3)]},
        "relationships": [{"target": "乙", "relation": "旧识", "attitude": "全书态度",
                           "note": top_note, "phase_attitudes": phase_attitudes}],
    })


def test_r2a_projected_relationship_keeps_only_phases_up_to_k():
    """R2a：投影卡里的 phase_attitudes 截到 ≤k —— 投影卡只装阶段 k 能看到的东西。"""
    from core.arc_view import project_card

    card = _rel_card([{"phase": p, "attitude": f"态度{p}", "note": f"口径{p}"} for p in (1, 2, 3)])
    for k in (1, 2, 3):
        proj, _ = project_card(card, k)
        phases = [pa.phase for pa in proj.relationships[0].phase_attitudes]
        assert phases == list(range(1, k + 1)), f"k={k} 投影卡里留着后期态度：{phases}"


def test_r2a_projected_phases_carry_label_state_only_at_last_phase_too():
    """R2a：k=n 时阶段表也只留 label/state —— overlay 已并进顶层，留着就是第二份来源。"""
    from core.arc_view import project_card
    from core.schema import CharacterCard

    card = CharacterCard.model_validate({"name": "甲", "character_arc": {"phases": [
        {"label": "前", "state": "s1", "overlay": {"personality_traits": ["前期性格"]}},
        {"label": "后", "state": "s2", "overlay": {"personality_traits": ["后期性格"]}}]}})
    proj, _ = project_card(card, 2)
    assert [p.overlay for p in proj.character_arc.phases] == [{}, {}], "k=n 时阶段里还留着 overlay"
    assert "前期性格" not in proj.model_dump_json(), "k=n 时投影卡里还有前一阶段的状态"
    assert proj.personality_traits[0] == "后期性格"


def test_r2b_note_never_falls_back_to_top_level_note():
    """R2b：有阶段态度时，口径只取 ≤k 里最近一条非空的；全部为空就是空，不回落顶层那句。

    顶层 note 由模型写，可能是全书（即后期）的立场；回落到它就把后期带进了早期阶段。
    """
    from core.arc_view import project_card

    card = _rel_card([{"phase": 1, "attitude": "疏远", "note": "初识口径"},
                      {"phase": 2, "attitude": "亲近", "note": ""}])
    proj, _ = project_card(card, 2)
    assert proj.relationships[0].note == "初识口径", "阶段 2 口径为空时没取 ≤k 最近的非空口径"

    blank = _rel_card([{"phase": 1, "attitude": "疏远", "note": ""}])
    proj, _ = project_card(blank, 1)
    assert proj.relationships[0].note == "", "回落到了顶层的全书口径"


def test_r2b_rules_pin_first_contact_and_per_phase_note():
    """R2b：关系口径写明「第一条写在开始有交集的阶段」「每条阶段态度都写 note」。

    投影靠「最早一条态度的阶段」判断两人何时认识、靠阶段 note 取口径 —— 生成侧不这么写，
    投影就会把人提前/推后，或拿不到阶段口径。
    """
    from core.relationship_batch import RELATIONSHIP_RULES

    assert "开始有交集的那个阶段" in RELATIONSHIP_RULES
    assert "每一条都写 note" in RELATIONSHIP_RULES
