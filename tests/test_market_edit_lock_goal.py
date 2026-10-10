# -*- coding: utf-8 -*-
"""广场卡编辑保存的版本锁 · 目标检查（docs/specs/examples-pending.md §1 第四部分）。

现状（main）：编辑一张已发布的卡再保存，后端不核对这次编辑依据的是哪一版 —— 两处同时改，
后保存的把先保存的整张盖掉，没有任何提示。

目标：编辑保存带上编辑所依据的那一版，卡变过就报「这张卡已在别处更新，请刷新后再改」，
什么都不写。恢复版本、更新发布本来就是整张覆盖，不带版本号，照旧。

走真实路由与真 PG（发布写多张表，以线上口径为准），夹具照 `test_market_publish_policy`。
期望文案是字面量。

L1 编辑保存带的是旧版本号 → 409，卡没变，没有多出版本记录，也没有走发布预审
L2 带的是当前版本号 → 成功，卡更新，多一条版本记录
L3 不带版本号（恢复版本 / 更新发布）→ 照旧成功（main 上就绿：回归守卫）
L4 广场卡详情页读卡的两个接口都带 revision，等于这张卡当前内容的版本号
L5 核对之后、写入之前卡又被改了 → 存储层不写、不落版本记录
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conftest import PG_ENV, TEST_DATABASE_URL
from core.card_out import card_revision
from core.moderation import publish_review as PR
from core.moderation.decision_engine import Decision, DecisionEngine
from deps import get_storage
from routers import card as C
from routers import market as M
from routers.auth import get_current_user, get_optional_user
from storage.postgres_store import PostgresStore

pytestmark = PG_ENV.skipif("广场卡编辑锁用例")

CONFLICT = "这张卡已在别处更新，请刷新后再改"
_CARD = {"name": "张三", "identity": "原来的"}


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


@pytest.fixture
async def owner(store):
    uid = f"u_{uuid.uuid4().hex[:12]}"
    await store.create_user(uid, f"n_{uuid.uuid4().hex[:12]}", "x")
    return uid


@pytest.fixture(autouse=True)
def _review_passes(monkeypatch):
    calls = []

    async def _review(card_json, llm, storage=None):
        calls.append(card_json)
        return {"content": {"pass": True, "reason": ""},
                "injection": {"pass": True, "reason": "", "error": False}}

    monkeypatch.setattr(M, "get_llm", lambda: object())
    monkeypatch.setattr(DecisionEngine, "decide", lambda self, *_a: Decision(
        decision="allow", confidence=0.5, tier="t", queue_priority=None))
    monkeypatch.setattr(PR, "auto_review_split", _review)
    return calls


def _client(store, uid) -> AsyncClient:
    app = FastAPI()
    app.include_router(M.router)
    app.include_router(C.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "username": "t", "role": "user"}
    app.dependency_overrides[get_optional_user] = lambda: {"id": uid, "username": "t", "role": "user"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _published(store, uid) -> str:
    """一张已发布的卡（公开、属主是 uid），返回它的 id。"""
    tid = f"txt_{uuid.uuid4().hex}"
    await store.save_text(tid, "src.txt", "content", user_id=uid)
    cid = f"card_{uuid.uuid4().hex}"
    await store.save_card(cid, tid, "张三", json.dumps(_CARD, ensure_ascii=False), user_id=uid)
    await store.update_card_visibility(cid, "public")
    return cid


async def _state(store, uid, cid) -> tuple[dict, int]:
    row = await store.get_card_owned(cid, uid)
    return json.loads(row["card_json"]), len(await store.get_card_versions_owned(cid, uid))


async def _save(store, uid, cid, identity, revision=None):
    body = {"card_json": json.dumps({**_CARD, "identity": identity}, ensure_ascii=False),
            "publish_message": "编辑更新"}
    if revision is not None:
        body["revision"] = revision
    async with _client(store, uid) as c:
        return await c.put(f"/api/market/{cid}/publish", json=body)


async def test_l1_saving_from_a_stale_version_is_a_conflict(store, owner, _review_passes):
    cid = await _published(store, owner)
    opened_at = card_revision((await store.get_card_owned(cid, owner))["card_json"])
    assert (await _save(store, owner, cid, "别处先改的", revision=opened_at)).status_code == 200
    reviews_before = len(_review_passes)

    r = await _save(store, owner, cid, "我后改的", revision=opened_at)

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == CONFLICT
    card, versions = await _state(store, owner, cid)
    assert (card["identity"], versions) == ("别处先改的", 1)
    assert len(_review_passes) == reviews_before          # 没有为一次注定失败的保存跑预审


async def test_l2_saving_from_the_current_version_goes_through(store, owner):
    cid = await _published(store, owner)
    current = card_revision((await store.get_card_owned(cid, owner))["card_json"])

    r = await _save(store, owner, cid, "我改的", revision=current)

    assert r.status_code == 200, r.text
    assert await _state(store, owner, cid) == ({**_CARD, "identity": "我改的"}, 1)


async def test_l3_overwrites_without_a_revision_still_work(store, owner):
    cid = await _published(store, owner)
    assert (await _save(store, owner, cid, "第一次")).status_code == 200

    r = await _save(store, owner, cid, "第二次")

    assert r.status_code == 200, r.text
    assert await _state(store, owner, cid) == ({**_CARD, "identity": "第二次"}, 2)


async def test_l4_both_card_detail_reads_carry_the_revision(store, owner):
    cid = await _published(store, owner)
    current = card_revision((await store.get_card_owned(cid, owner))["card_json"])

    async with _client(store, owner) as c:
        first_load = await c.get(f"/api/cards/{cid}/detail")       # 详情页打开时读的
        reload = await c.get(f"/api/market/card/{cid}")            # 保存、恢复版本之后重读的

    assert (first_load.status_code, reload.status_code) == (200, 200), (first_load.text, reload.text)
    assert (first_load.json()["revision"], reload.json()["revision"]) == (current, current)


async def test_l5_the_store_writes_nothing_when_the_card_changed_after_the_check(store, owner):
    cid = await _published(store, owner)
    read = (await store.get_card_owned(cid, owner))["card_json"]
    await store.update_card(cid, {**_CARD, "identity": "夹在中间的写入"}, expected=read)

    out = await store.update_published_card(
        cid, owner, json.dumps({**_CARD, "identity": "依据旧内容的写入"}, ensure_ascii=False),
        "", "", "编辑更新", read)

    assert out is None
    assert await _state(store, owner, cid) == ({**_CARD, "identity": "夹在中间的写入"}, 0)
