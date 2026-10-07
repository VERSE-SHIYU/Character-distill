# -*- coding: utf-8 -*-
"""未定位条目挪进阶段（段 3）—— spec `docs/specs/arc-phase-unlocated.md` §6。

局部改动：只有目标检查 + 本文件，不设变异驱动（返工经验 #10）。每条用例一条 assert。
"""
from __future__ import annotations

import json

import pytest

from core.arc_view import project_card
from core.card_layers import get_path
from core.schema import CharacterCard, UnlocatedAttitude
from core.unlocated import move_unlocated


def _loose_card(**unlocated):
    return CharacterCard.model_validate({"name": "x", "character_arc": {
        "phases": [{"state": "s"}], "unlocated": unlocated}})


def _pa(card):
    return [(pa.phase, pa.attitude) for pa in card.relationships[0].phase_attitudes]


# ── M：挪进阶段（用户给了依据） ───────────────────────────────────────

def _movable():
    return _loose_card(
        behaviors=[{"situation": "s", "behavior": "b"}],
        overlay={"personality_traits": ["多疑"], "speaking_style": {"tone": ["冷", "热"]}},
        attitudes=[{"target": "甲", "attitude": "挪来的", "note": "口径"}],
    ).model_copy(update={"relationships": [
        {"target": "甲", "relation": "友", "attitude": "", "note": "",
         "phase_attitudes": [{"phase": 1, "attitude": "", "note": ""}]}]})


_B = {"situation": "s", "behavior": "b"}
_A = {"target": "甲", "attitude": "挪来的", "phase": 0}


def _movable_card():
    return CharacterCard.model_validate(_movable().model_dump())


def test_m1_behavior_moves_into_phase():
    card = move_unlocated(_movable_card(), section="behaviors", index=0, phase=1, expected=_B)
    assert ([b.situation for b in card.character_arc.phases[0].behaviors],
            card.character_arc.unlocated.behaviors) == (["s"], [])


def test_m2_list_value_is_appended_and_leaf_pruned():
    card = move_unlocated(_movable_card(), section="overlay", path="personality_traits",
                          index=0, phase=1, expected="多疑")
    assert (card.character_arc.phases[0].overlay,
            get_path(card.character_arc.unlocated.overlay, "personality_traits")) == (
        {"personality_traits": ["多疑"]}, None)


def test_m3_scalar_value_into_empty_slot():
    card = move_unlocated(_movable_card(), section="overlay", path="speaking_style.tone",
                          index=0, phase=1, expected="冷")
    assert (card.character_arc.phases[0].overlay,
            card.character_arc.unlocated.overlay["speaking_style"]) == (
        {"speaking_style": {"tone": "冷"}}, {"tone": ["热"]})


def test_m4_scalar_value_into_occupied_slot_swaps_back():
    card = move_unlocated(_movable_card(), section="overlay", path="speaking_style.tone",
                          index=0, phase=1, expected="冷")
    card = move_unlocated(card, section="overlay", path="speaking_style.tone", index=0, phase=1,
                          expected="热")
    assert (get_path(card.character_arc.phases[0].overlay, "speaking_style.tone"),
            get_path(card.character_arc.unlocated.overlay, "speaking_style.tone")) == ("热", ["冷"])


def test_m5_attitude_replaces_empty_placeholder_and_shows_in_projection():
    card = move_unlocated(_movable_card(), section="attitudes", index=0, phase=1, expected=_A)
    rel = project_card(card, 1)[0].relationships[0]
    assert ((rel.attitude, rel.note), card.character_arc.unlocated.attitudes) == (
        ("挪来的", "口径"), [])


def test_m6_attitude_into_phase_with_attitude_swaps_back():
    card = move_unlocated(_movable_card(), section="attitudes", index=0, phase=1, expected=_A)
    card = card.model_copy(deep=True)
    card.character_arc.unlocated.attitudes.append(UnlocatedAttitude(target="甲", attitude="第二条"))
    card = move_unlocated(card, section="attitudes", index=0, phase=1,
                          expected={"target": "甲", "attitude": "第二条"})
    assert (_pa(card), [a.attitude for a in card.character_arc.unlocated.attitudes]) == (
        [(1, "第二条")], ["挪来的"])


@pytest.mark.parametrize("kw", [
    {"section": "behaviors", "index": 5, "phase": 1, "expected": _B},
    {"section": "behaviors", "index": 0, "phase": 2, "expected": _B},
    {"section": "overlay", "path": "key_memories", "index": 0, "phase": 1, "expected": "x"},
    {"section": "nope", "index": 0, "phase": 1, "expected": _B},
    # 序号还在、内容变了（另一个标签页先挪过 / 编辑过）：拒绝，不挪错（审计发现 7）
    {"section": "behaviors", "index": 0, "phase": 1, "expected": {"situation": "s", "behavior": "旧"}},
    {"section": "behaviors", "index": 0, "phase": 1, "expected": {}},
    {"section": "overlay", "path": "speaking_style.tone", "index": 0, "phase": 1, "expected": "热"},
    {"section": "attitudes", "index": 0, "phase": 1, "expected": {"target": "乙", "attitude": "挪来的"}},
])
def test_m7_invalid_move_is_rejected(kw):
    with pytest.raises(ValueError):
        move_unlocated(_movable_card(), **kw)


def test_m8_moved_item_reaches_the_projection_of_that_phase():
    """pytest 半段（Playwright 只看界面）：挪进阶段 1 后，投影到阶段 1 的卡里有它。"""
    card = move_unlocated(_movable_card(), section="overlay", path="personality_traits",
                          index=0, phase=1, expected="多疑")
    assert "多疑" in project_card(card, 1)[0].personality_traits


# ── 路由：属主挪动并落库；非属主 404 ─────────────────────────────────

class _Store:
    def __init__(self, card):
        self.rows = {"c1": {"user_id": "u1", "card_json": card.model_dump_json()}}

    async def get_card_owned(self, card_id, user_id):
        row = self.rows.get(card_id)
        return row if row and row["user_id"] == user_id else None

    async def update_card(self, card_id, card_json):
        self.rows[card_id]["card_json"] = json.dumps(card_json, ensure_ascii=False)
        return {"id": card_id, "card_json": self.rows[card_id]["card_json"]}   # 同 PG：回整行


def _client(store, user_id):
    from fastapi import FastAPI

    from conftest import app_lifespan, open_test_client
    from deps import get_storage
    from routers.auth import get_current_user
    from routers.distill import router

    app = FastAPI(lifespan=app_lifespan)
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "role": "user"}
    return open_test_client(app)


def test_route_owner_move_persists():
    store = _Store(_movable_card())
    r = _client(store, "u1").post("/api/distill/card/c1/unlocated/move",
                                  json={"section": "behaviors", "index": 0, "phase": 1, "expected": _B})
    saved = CharacterCard.model_validate_json(store.rows["c1"]["card_json"])
    assert (r.status_code, [b.situation for b in saved.character_arc.phases[0].behaviors]) == (
        200, ["s"])


def test_route_returns_the_card_through_out_card():
    """B4：出卡只经 `out_card` —— 返回的卡带现算的 `selectable`。"""
    r = _client(_Store(_movable_card()), "u1").post(
        "/api/distill/card/c1/unlocated/move", json={"section": "behaviors", "index": 0, "phase": 1, "expected": _B})
    assert "selectable" in json.loads(r.json()["card"]["card_json"])["character_arc"]


def test_route_intruder_gets_404():
    r = _client(_Store(_movable_card()), "u2").post(
        "/api/distill/card/c1/unlocated/move", json={"section": "behaviors", "index": 0, "phase": 1, "expected": _B})
    assert r.status_code == 404


def test_route_stale_index_is_400():
    r = _client(_Store(_movable_card()), "u1").post(
        "/api/distill/card/c1/unlocated/move", json={"section": "behaviors", "index": 9, "phase": 1, "expected": _B})
    assert r.status_code == 400


def test_route_drifted_content_is_400_and_card_untouched():
    """序号漂移：同一序号处已是别的条目 → 400，卡不写回（审计发现 7）。"""
    store = _Store(_movable_card())
    before = store.rows["c1"]["card_json"]
    r = _client(store, "u1").post("/api/distill/card/c1/unlocated/move", json={
        "section": "behaviors", "index": 0, "phase": 1,
        "expected": {"situation": "s", "behavior": "别的"}})
    assert (r.status_code, store.rows["c1"]["card_json"]) == (400, before)
