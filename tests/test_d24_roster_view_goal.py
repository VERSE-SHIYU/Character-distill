# -*- coding: utf-8 -*-
"""D24 ①b 回归：进角色页看**已存的**名单不花钱（``GET /api/distill/roster/{text_id}``）。

#235 把「重新识别」改成 ``force=True`` 之后，有卡的书再没有免费显示名单的入口：前端进页
只读卡片、把名单置空（``useAppStore.js`` ``selectText`` / ``openCharacterList``），要看名单
只能点「重新识别」—— 那会重算并扣费。本文件锁只读接口：

  1. 库里有当前版本的名单 → 原样返回，**模型调用 0 次**；
  2. 版本不符 / 没有名单 → 空列表，同样不调模型（不在这里顺手识别）；
  3. 别人的书 → 404（属主校验在读缓存之前，同 ``POST /identify``）。
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

import deps
from conftest import TEST_DATABASE_URL
from core.distiller import Distiller
from deps import get_storage
from routers.auth import get_current_user
from routers.distill import router as distill_router
from storage.postgres_store import PostgresStore

ROSTER = [{"name": "云绮", "aliases": ["绮绮"]}, {"name": "祈灼", "aliases": []}]


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


async def _user(store) -> str:
    uid = f"u_{uuid.uuid4().hex[:12]}"
    await store.create_user(uid, f"n_{uuid.uuid4().hex[:12]}", "x")
    return uid


async def _text(store, uid) -> str:
    tid = f"txt_{uuid.uuid4().hex}"
    await store.save_text(tid, "src.txt", "正文", user_id=uid)
    return tid


@pytest.fixture
def no_llm(monkeypatch):
    """取模型 / 取蒸馏器一律算失败：只读接口一次都不该碰它们。"""
    calls: list[str] = []

    async def _user_llm(*a, **kw):
        calls.append("get_user_llm")
        raise AssertionError("读名单不应取模型")

    def _distiller(*a, **kw):
        calls.append("get_distiller")
        raise AssertionError("读名单不应建蒸馏器")

    monkeypatch.setattr(deps, "get_user_llm", _user_llm)
    monkeypatch.setattr(deps, "get_distiller", _distiller)
    return calls


async def _get(store, uid, tid):
    app = FastAPI()
    app.include_router(distill_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "u", "role": "user"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(f"/api/distill/roster/{tid}")


async def test_stored_roster_is_returned_without_llm(store, no_llm):
    uid = await _user(store)
    tid = await _text(store, uid)
    await store.save_characters(tid, ROSTER, version=Distiller.IDENTIFY_VERSION)

    r = await _get(store, uid, tid)

    assert r.status_code == 200, r.text
    assert r.json() == {"characters": ROSTER}
    assert no_llm == []


@pytest.mark.parametrize("version", [None, Distiller.IDENTIFY_VERSION - 1])
async def test_missing_or_stale_roster_is_empty_and_not_reidentified(store, no_llm, version):
    uid = await _user(store)
    tid = await _text(store, uid)
    if version is not None:
        await store.save_characters(tid, ROSTER, version=version)

    r = await _get(store, uid, tid)

    assert r.status_code == 200, r.text
    assert r.json() == {"characters": []}
    assert no_llm == []


async def test_other_users_roster_is_not_visible(store, no_llm):
    owner, other = await _user(store), await _user(store)
    tid = await _text(store, owner)
    await store.save_characters(tid, ROSTER, version=Distiller.IDENTIFY_VERSION)

    r = await _get(store, other, tid)

    assert r.status_code == 404, r.text
    assert "云绮" not in r.text
