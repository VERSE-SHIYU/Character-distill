# -*- coding: utf-8 -*-
"""锁：群聊成员只能是同一本书里的不同角色，至少 2 个；其余一律拒绝。

形状（至少 2 张卡）由请求 schema 判，不满足 → 422；要查库才能判的规则
（同一本书、不是独立卡、同一角色只选一个版本）只在 `group._check_group_members` 一处判，
不满足 → 400，且不建群。

Run: pytest tests/test_group_members.py -v
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from routers.group import CreateGroupRequest, _check_group_members


def _card(name: str, text_id: str | None = "t1") -> dict:
    return {"id": f"c_{name}", "name": name, "text_id": text_id}


def test_two_characters_of_one_book_are_allowed():
    assert _check_group_members([_card("魏无羡"), _card("江澄")]) == "t1"


@pytest.mark.parametrize("cards, why", [
    ([_card("魏无羡"), _card("江澄", text_id="t2")], "同一本书"),
    ([_card("魏无羡"), _card("江澄", text_id=None)], "独立角色卡"),
    ([_card("魏无羡"), _card("江澄", text_id="")], "独立角色卡"),
    ([_card("魏无羡"), {**_card("魏无羡"), "id": "c_v2"}], "同一个角色"),
    ([_card("魏无羡")], "至少需要"),
])
def test_everything_else_is_rejected(cards, why):
    with pytest.raises(HTTPException) as exc:
        _check_group_members(cards)
    assert exc.value.status_code == 400
    assert why in exc.value.detail, exc.value.detail


def test_request_needs_at_least_two_cards():
    with pytest.raises(ValidationError):
        CreateGroupRequest(card_ids=["c1"])
    assert CreateGroupRequest(card_ids=["c1", "c2"]).card_ids == ["c1", "c2"]


def test_create_rejects_cards_from_two_books_and_builds_nothing(monkeypatch):
    import asyncio
    import uuid

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import routers.group as G
    from deps import get_group_sessions, get_storage
    from routers.auth import get_current_user
    from storage.sqlite_store import SQLiteStore

    async def _llm(*_a, **_kw):
        return object()

    import tempfile
    store = SQLiteStore(f"{tempfile.mkdtemp()}/g.db")
    uid = f"u_{uuid.uuid4().hex[:8]}"
    cards = []
    for i in (1, 2):
        tid = f"t{i}_{uuid.uuid4().hex}"
        cid = f"c{i}_{uuid.uuid4().hex}"
        asyncio.run(store.save_text(tid, "src.txt", "content", user_id=uid))
        asyncio.run(store.save_card(cid, tid, f"角色{i}", f'{{"name": "角色{i}"}}', user_id=uid))
        cards.append(cid)

    monkeypatch.setattr(G, "get_user_llm", _llm)
    app = FastAPI()
    app.include_router(G.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "role": "user"}
    before = set(get_group_sessions())

    r = TestClient(app).post("/api/group/create", json={"card_ids": cards})

    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "群聊只能选同一本书里的角色"
    assert set(get_group_sessions()) == before, "规则不满足却建出了群"
    assert asyncio.run(store.list_group_sessions(uid)) == [], "规则不满足却落了库"
