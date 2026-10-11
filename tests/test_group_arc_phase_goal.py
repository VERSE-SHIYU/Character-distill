# -*- coding: utf-8 -*-
"""目标检查：群聊里每个 AI 人物按建群时选的时期说话（spec docs/specs/group-arc-phase.md §1）。

全部从真接口（`/api/group/create`、`/api/group/list`）、真 PG 进去；重启用「清内存表 →
走属主重建 `_get_owned_group`」模拟（与线上同一条路）。每一条都同时断言「选了的人物到了
阶段 k」和「没选的人物仍是最后阶段」，单边改坏也会红。

用 `AsyncClient` 不用 `TestClient`：asyncpg 的连接池绑定创建它的 loop（同 test_comment_likes）。

Run: pytest tests/test_group_arc_phase_goal.py -v
"""
from __future__ import annotations

import copy
import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conftest import PG_ENV, TEST_DATABASE_URL

_pg = PG_ENV.skipif("PG 群聊阶段目标检查")

# 两阶段的可选卡：起点齐全 + 有指纹（`has_positions` 为真）。
_ARC = {"axis": "从冷到热", "source_fingerprint": "FP", "phases": [
    {"label": "冷", "state": "起初", "start": 0},
    {"label": "热", "state": "后来", "start": 10},
]}
# 不可选的卡：缺指纹 → `valid_phase` 恒 None（一对一同规则，DA8）。
_ARC_UNSELECTABLE = {"axis": "从冷到热", "phases": [
    {"label": "冷", "state": "起初", "start": 0},
    {"label": "热", "state": "后来", "start": 10},
]}


def _card_json(name, arc):
    return json.dumps({"name": name, "character_arc": copy.deepcopy(arc)}, ensure_ascii=False)


@pytest.fixture
async def world(monkeypatch):
    """一本书 + 三张卡（甲、乙可选阶段；丙不可选）+ 只装群聊路由的 app。"""
    import deps
    import routers.group as group_mod
    from routers.auth import get_current_user
    from storage.postgres_store import PostgresStore
    from test_session_identity_injection import _MemMgr, _NoopRAG, _ReplyLLM

    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    uid = f"usr_{uuid.uuid4().hex[:12]}"

    async def _fake_user_llm(*_a, **_kw):
        return _ReplyLLM()

    monkeypatch.setattr(deps, "get_llm", _ReplyLLM)
    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(group_mod, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_memory_manager", lambda: _MemMgr())
    monkeypatch.setattr(
        deps, "get_rag_config",
        lambda: {"chunk_size": 500, "chunk_overlap": 50, "top_k": 3})
    monkeypatch.setattr("core.rag.RAGEngine", lambda *_a, **_kw: _NoopRAG())
    monkeypatch.setattr(deps, "_storage", store)

    app = FastAPI()
    app.include_router(group_mod.router)
    app.dependency_overrides[deps.get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "testuser", "role": "user"}

    await store.create_user(uid, f"n_{uid}", "x")
    tid = f"txt_{uuid.uuid4().hex}"
    await store.save_text(tid, "src.txt", "content", user_id=uid)
    ids = {}
    for name, arc in (("甲", _ARC), ("乙", _ARC), ("丙", _ARC_UNSELECTABLE)):
        cid = f"card_{uuid.uuid4().hex}"
        await store.save_card(cid, tid, name, _card_json(name, arc), user_id=uid)
        ids[name] = cid
    try:
        yield {"app": app, "store": store, "uid": uid, "ids": ids, "group_mod": group_mod}
    finally:
        await store.close()


async def _call(world, method, path, body=None):
    async with AsyncClient(transport=ASGITransport(app=world["app"]), base_url="http://t") as ac:
        return await ac.request(method, path, json=body)


async def _create(world, names, phases, **persona):
    ids = world["ids"]
    body = {"card_ids": [ids[n] for n in names],
            "member_phases": {ids[n]: k for n, k in phases.items()},
            "user_persona_type": "stranger", "user_persona_name": "路人"}
    body.update(persona)
    r = await _call(world, "POST", "/api/group/create", body)
    assert r.status_code == 200, r.text
    return r.json()["group"]["id"]


def _labels(engine):
    return [p.label for p in engine.card.character_arc.phases]


def _live(gid):
    from deps import get_group_sessions
    return get_group_sessions()[gid]


async def _restarted(world, gid):
    """模拟服务重启：内存表里没有这个群 → 走属主重建（与线上同一条路）。"""
    from deps import get_group_sessions
    get_group_sessions().pop(gid, None)
    group = await world["group_mod"]._get_owned_group(gid, world["uid"], world["store"])
    assert group is not None, "重建失败"
    return group


# ── G1 建群：选了阶段 1 的人物只投影到阶段 1；没选的仍是最后阶段 ──────────────

@_pg
async def test_g1_create_projects_each_member_to_its_phase(world):
    gid = await _create(world, ["甲", "乙"], {"甲": 1})
    group, ids = _live(gid), world["ids"]
    assert _labels(group.engines[ids["甲"]]) == ["冷"], "甲选了阶段 1，引擎没投影到阶段 1"
    assert group.engines[ids["甲"]].arc_view.boundary, "甲在阶段 1，应带边界说明"
    assert _labels(group.engines[ids["乙"]]) == ["冷", "热"], "乙没选，应是最后阶段"
    assert not group.engines[ids["乙"]].arc_view.boundary


# ── G2 重启：从库里重建后，每个人物的阶段不变 ──────────────────────────────────

@_pg
async def test_g2_restart_keeps_each_members_phase(world):
    gid = await _create(world, ["甲", "乙"], {"甲": 1})
    group, ids = await _restarted(world, gid), world["ids"]
    assert _labels(group.engines[ids["甲"]]) == ["冷"], "重启后甲悄悄变回了最后阶段"
    assert _labels(group.engines[ids["乙"]]) == ["冷", "热"]


# ── G3 不合法的阶段一律按最后阶段，且不落库 ──────────────────────────────────

@_pg
@pytest.mark.parametrize("bad", [0, 3, -1])
async def test_g3_out_of_range_phase_falls_back_to_last(world, bad):
    gid = await _create(world, ["甲", "乙"], {"甲": bad, "乙": 1})
    ids = world["ids"]
    for group in (_live(gid), await _restarted(world, gid)):
        assert _labels(group.engines[ids["甲"]]) == ["冷", "热"], f"越界 {bad} 应按最后阶段"
        assert _labels(group.engines[ids["乙"]]) == ["冷"], "合法的那一个不能被连带丢掉"
    row = await world["store"].get_group_session_owned(gid, world["uid"])
    assert row["member_phases"] == {ids["乙"]: 1}, f"不合法的阶段不应落库：{row['member_phases']}"


@_pg
async def test_g3_unselectable_card_ignores_phase(world):
    gid = await _create(world, ["甲", "丙"], {"丙": 1, "甲": 1})
    ids = world["ids"]
    for group in (_live(gid), await _restarted(world, gid)):
        assert _labels(group.engines[ids["丙"]]) == ["冷", "热"], "不可选阶段的卡只按最后阶段"
        assert _labels(group.engines[ids["甲"]]) == ["冷"]


# ── G4 建群响应就是这一行群（与列群返回同一形状），带回每个人物的阶段 ───────────
#     建群与点开已有群在前端走同一条 enterGroup —— 两边形状必须相同，加字段只改一处（补充 R4）。

@_pg
async def test_g4_create_returns_the_same_row_as_list(world):
    ids = world["ids"]
    r = await _call(world, "POST", "/api/group/create", {
        "card_ids": [ids["甲"], ids["乙"]], "member_phases": {ids["甲"]: 1},
        "user_persona_type": "stranger", "user_persona_name": "路人"})
    assert r.status_code == 200, r.text
    created = r.json()["group"]
    assert created["member_phases"] == {ids["甲"]: 1}, created
    r = await _call(world, "GET", "/api/group/list")
    assert r.status_code == 200, r.text
    listed = next(x for x in r.json()["groups"] if x["id"] == created["id"])
    assert set(created) == set(listed), (
        f"建群返回与列群返回不是同一形状：只在建群 {set(created) - set(listed)}，"
        f"只在列群 {set(listed) - set(created)}")
    for k in ("name", "card_ids", "user_persona_type", "user_persona_card_id",
              "user_persona_name", "user_persona_desc", "member_phases"):
        assert created[k] == listed[k], (k, created[k], listed[k])


# ── G5 用户扮演的人物不选时期：传了也不落库，不影响 AI 成员 ────────────────────

@_pg
async def test_g5_played_character_phase_is_not_stored(world):
    ids = world["ids"]
    gid = await _create(world, ["甲", "乙"], {"甲": 1, "乙": 1},
                        user_persona_type="character", user_persona_card_id=ids["甲"],
                        user_persona_name="")
    row = await world["store"].get_group_session_owned(gid, world["uid"])
    assert row["member_phases"] == {ids["乙"]: 1}, f"扮演的人物不该有时期：{row['member_phases']}"
    assert ids["甲"] not in _live(gid).engines
    assert _labels(_live(gid).engines[ids["乙"]]) == ["冷"]


# ── G6 提示词：阶段 1 的人物带边界说明、不含后面阶段；最后阶段的人物不带 ─────────

@_pg
async def test_g6_prompt_follows_member_phase(world):
    from core.context_engine import _BOUNDARY_NOTICE
    gid = await _create(world, ["甲", "乙"], {"甲": 1})
    group, ids = await _restarted(world, gid), world["ids"]
    early = str(group.engines[ids["甲"]]._compose_context("你好", include_dynamic=False))
    late = str(group.engines[ids["乙"]]._compose_context("你好", include_dynamic=False))
    assert _BOUNDARY_NOTICE in early, "阶段 1 的人物提示词里没有边界说明"
    assert "后来" not in early, "阶段 1 的人物提示词里出现了阶段 2 的状态"
    assert _BOUNDARY_NOTICE not in late, "最后阶段的人物不该带边界说明"
    assert "后来" in late


# ── G7 恢复群用库里的阶段，不再过 valid_phase（与一对一恢复同口径，补充 R5）──────
#     建群时卡可选、存了阶段 1；之后卡变得不可选（缺指纹）→ 重建仍在阶段 1。
#     谁在恢复路径「顺手」加上 valid_phase，这条就红。

@_pg
async def test_g7_restore_keeps_stored_phase_even_if_card_became_unselectable(world):
    ids = world["ids"]
    gid = await _create(world, ["甲", "乙"], {"甲": 1})
    text_id = (await world["store"].get_card_owned(ids["甲"], world["uid"]))["text_id"]
    saved = await world["store"].save_card(
        ids["甲"], text_id, "甲", _card_json("甲", _ARC_UNSELECTABLE), user_id=world["uid"])
    assert (saved or {}).get("id", ids["甲"]) == ids["甲"], "夹具没改到同一张卡"
    rec = await world["store"].get_card_owned(ids["甲"], world["uid"])
    assert "source_fingerprint" not in json.loads(rec["card_json"])["character_arc"], "夹具没生效"
    group = await _restarted(world, gid)
    assert _labels(group.engines[ids["甲"]]) == ["冷"], "恢复群对库里的阶段又做了 valid_phase 校验"

