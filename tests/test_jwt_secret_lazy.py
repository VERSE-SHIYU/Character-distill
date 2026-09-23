# -*- coding: utf-8 -*-
"""95：`JWT_SECRET` 未配置时，**匿名**请求不该被适配器连带成 500。

FastAPI 在进端点函数体之前解析整棵依赖树，`Depends` 的求值与请求带不带凭据无关。
`get_current_user` / `get_optional_user` / `register` 原本写的是
`secret: str = Depends(get_jwt_secret)` —— 于是**匿名**请求也会把 secret 读一遍，而
`get_jwt_secret()` 在未配置时抛 RuntimeError ⇒ 一条本该正常的公开读变成 500。

改法是把它换成取值函数（`jwt_secret_source` 返回 `get_jwt_secret` 本身，不调用），
由 `resolve_identity` 在判完「没凭据」之后才按需取值。三条对照里
`/api/market/card/{id}` 是唯一的探针：它的依赖树里有适配器，tags 没有，history 被中间件
在路由之前拦下 —— 三者一起才说明白「读 secret 的时机」而不是「有没有报错」。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

import deps
import server
from routers.auth import _create_access_token
from storage.sqlite_store import SQLiteStore

_PW_HASH = PasswordHash.recommended().hash("Pass1234")
_SECRET = "p" * 40


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "lazy_secret.db"))


@pytest.fixture
def user(store):
    uid = f"usr_{uuid.uuid4().hex[:16]}"
    return _run(store.create_user(uid, "Lazy_" + uuid.uuid4().hex[:6], _PW_HASH))


@pytest.fixture
def client(store, user, monkeypatch):
    monkeypatch.setattr(deps, "_storage", store)
    return TestClient(server.app, raise_server_exceptions=False)


def _no_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)


class TestNoSecretConfigured:
    def test_public_read_in_an_adapter_dep_tree_is_404_not_500(self, client, monkeypatch):
        """本条的探针：依赖树里有适配器，但请求匿名 —— 不该读 secret，更不该 500。"""
        _no_secret(monkeypatch)
        r = client.get("/api/market/card/nope")
        assert r.status_code == 404, f"本该是「卡片不存在」，实得 {r.status_code}：{r.text}"

    def test_public_read_without_an_adapter_is_unaffected(self, client, monkeypatch):
        """对照：这条从来就没错过，它证明上一条不是「整个 app 都挂」的假象。"""
        _no_secret(monkeypatch)
        assert client.get("/api/market/tags").status_code == 200

    def test_protected_path_without_credentials_is_401(self, client, monkeypatch):
        """对照：中间件在路由前就拦下，secret 轮不到读 —— 401 而不是 500。"""
        _no_secret(monkeypatch)
        assert client.get("/api/history/list").status_code == 401


class TestWithSecretConfigured:
    def test_a_valid_token_still_authenticates(self, client, user, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", _SECRET)
        tok = _create_access_token(user["id"], user["username"], _SECRET)
        r = client.get("/api/history/list", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text

    def test_a_bad_token_is_still_rejected(self, client, user, monkeypatch):
        """惰性化不能顺手把验签放过去。"""
        monkeypatch.setenv("JWT_SECRET", _SECRET)
        r = client.get("/api/history/list", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401
