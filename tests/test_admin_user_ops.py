# -*- coding: utf-8 -*-
"""管理员禁用 / 启用 / 封禁：三条本地路由共用同一组前置条件（spec admin-peer-disable）。

三条前置条件，每条路由都要满足：
  1. 目标用户不存在 → 404，不是「写了 0 行却回 ok」；
  2. 禁用 / 封禁自己 → 400，库里状态不变；
  3. 操作者只取登录身份，请求体里的 `admin_id` 不算数。

另钉一条生效口径：禁用之后，该用户的下一次请求在鉴权处被拒（403），不需要吊销会话。

**为什么用真 PG**：「写了 0 行」这件事只有真库答得了；SQLite 侧不测（AGENTS.md）。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conftest import PG_ENV, TEST_DATABASE_URL
from core import roles
from deps import get_storage
from routers import admin as A
from routers.auth import (
    IDENTITY_REJECTIONS,
    Verdict,
    _create_access_token,
    get_current_user,
    get_jwt_secret,
    resolve_identity,
)
from storage.postgres_store import PostgresStore

pytestmark = PG_ENV.skipif("管理员禁用/封禁用例")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


async def _user(store, role=roles.USER) -> str:
    uid = _uid("u")
    await store.create_user(uid, _uid("n"), "x")
    await store.set_user_role(uid, role)
    return uid


def _app(store, operator_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[get_storage] = lambda: store

    async def _live():
        return await store.get_user_by_id(operator_id)

    app.dependency_overrides[get_current_user] = _live
    return app


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _disabled(store, uid) -> bool:
    return bool((await store.get_user_by_id(uid) or {}).get("is_disabled"))


# ── 1. 目标不存在 → 404 ────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["disable", "enable", "ban"])
async def test_unknown_target_is_404(store, action):
    admin = await _user(store, roles.ADMIN)
    async with _client(_app(store, admin)) as c:
        r = await c.post(f"/api/admin/users/{_uid('ghost')}/{action}", json={})
    assert r.status_code == 404, f"{action} 不存在的用户必须 404，实得 {r.status_code} {r.text}"


# ── 2. 不能禁用 / 封禁自己 ────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["disable", "ban"])
async def test_cannot_target_self(store, action):
    admin = await _user(store, roles.ADMIN)
    async with _client(_app(store, admin)) as c:
        r = await c.post(f"/api/admin/users/{admin}/{action}", json={})
    assert r.status_code == 400, f"{action} 自己必须 400，实得 {r.status_code} {r.text}"
    assert not await _disabled(store, admin), "被拒之后库里状态不能变"


# ── 3. 封禁的操作者只取登录身份 ───────────────────────────────────────────

async def test_ban_operator_is_the_logged_in_admin(store, monkeypatch):
    admin = await _user(store, roles.ADMIN)
    target = await _user(store)
    seen: list[str] = []
    real = store.ban_user_and_contents

    async def _spy(user_id, admin_id):
        seen.append(admin_id)
        return await real(user_id, admin_id)

    monkeypatch.setattr(store, "ban_user_and_contents", _spy)
    async with _client(_app(store, admin)) as c:
        r = await c.post(f"/api/admin/users/{target}/ban", json={"admin_id": "forged"})
    assert r.status_code == 200, r.text
    assert seen == [admin], f"操作者必须是登录身份 {admin}，实得 {seen}"


# ── 4. 禁用 / 启用本身照常生效 ───────────────────────────────────────────

async def test_disable_then_enable_round_trip(store):
    admin = await _user(store, roles.ADMIN)
    target = await _user(store)
    async with _client(_app(store, admin)) as c:
        assert (await c.post(f"/api/admin/users/{target}/disable", json={})).status_code == 200
        assert await _disabled(store, target)
        assert (await c.post(f"/api/admin/users/{target}/enable", json={})).status_code == 200
        assert not await _disabled(store, target)


# ── 5. 生效口径：下一次请求即 403，不需要吊销会话 ───────────────────────

async def test_disabled_user_is_refused_on_next_request(store):
    admin = await _user(store, roles.ADMIN)
    target = await _user(store)
    token = _create_access_token(target, "n", get_jwt_secret())
    verdict, _ = await resolve_identity(token, get_jwt_secret, store)
    assert verdict is Verdict.OK, "前提：禁用前这张令牌是有效的"

    async with _client(_app(store, admin)) as c:
        assert (await c.post(f"/api/admin/users/{target}/disable", json={})).status_code == 200

    verdict, _ = await resolve_identity(token, get_jwt_secret, store)
    assert verdict is Verdict.DISABLED
    assert IDENTITY_REJECTIONS[verdict][0] == 403
