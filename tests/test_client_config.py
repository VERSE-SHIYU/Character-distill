# -*- coding: utf-8 -*-
"""前端上报配置的单一来源（`web/client_config.py`）。

判据对应步骤 3 那四条：DSN 空 → 接口给 `{}` 且 CSP 与接线前逐字一致；DSN 非空 → 接口给
DSN 且 CSP 放行那个 origin；未登录可访问；演示账号可访问。

**接口与 CSP 一起断言**，不分开测：这两处若各读一次环境变量，就会出现「接口给了新 DSN、
CSP 还放着旧的」的半通状态 —— 那时只测接口的话本文件照样全绿。同一个响应里同时取 body
与头，测的才是「同一个来源」这件事。

**被测对象是生产 app 本身**（`server.app`），理由同 `tests/test_demo_gate.py`：路由与
中间件都是在 `server` 导入期装到那个 app 上的，另搭一个 app 只是把装配再调一次。

Run: pytest tests/test_client_config.py -v
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

import deps
import server
from core import roles
from core.node import node_region
from routers.auth import _create_access_token, get_jwt_secret
from storage.sqlite_store import SQLiteStore
from web.client_config import (
    SENTRY_FRONTEND_DSN_ENV,
    SENTRY_RELEASE_ENV,
    sentry_frontend_dsn,
    sentry_origin,
)

DSN = "https://public-probe@errors.example.test/7"
ORIGIN = "https://errors.example.test"
PATH = "/api/client-config"
CSP_BASE_CONNECT_SRC = "connect-src 'self' https://api.deepseek.com"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _csp(r) -> str:
    return r.headers["Content-Security-Policy"]


@pytest.fixture
def no_dsn(monkeypatch):
    """两根环境变量都清掉 —— 缺省态，也是**没接线**时生产该有的样子。"""
    monkeypatch.delenv(SENTRY_FRONTEND_DSN_ENV, raising=False)
    monkeypatch.delenv(SENTRY_RELEASE_ENV, raising=False)


@pytest.fixture
def client():
    """裸 `TestClient`（不进 `with`）：本文件不测 lifespan 装的东西，只发 GET。

    `raise_server_exceptions=False`：扫描要的是**响应**，端点内部炸了该看到 500，
    而不是把整条用例掀掉。
    """
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.fixture
def guest_headers(tmp_path, monkeypatch):
    """一个**演示账号**的凭据。

    身份来自库里的 `role` 列（经 `set_user_role` 落库）—— 门禁读的是中间件每请求解析
    出来的身份，不写进库就不是同一个事实。
    """
    store = SQLiteStore(str(tmp_path / "client_config.db"))
    uid = f"usr_{uuid.uuid4().hex[:16]}"
    username = "Guest_" + uuid.uuid4().hex[:6]
    _run(store.create_user(uid, username, PasswordHash.recommended().hash("Pass1234")))
    _run(store.set_user_role(uid, roles.GUEST))
    monkeypatch.setattr(deps, "_storage", store)
    return {"Authorization": f"Bearer {_create_access_token(uid, username, get_jwt_secret())}"}


# ═══════════════════════════════════════════════════════════════════════════════
# 纯函数层：DSN → origin
# ═══════════════════════════════════════════════════════════════════════════════

def test_origin_is_the_dsn_host_not_the_dsn(no_dsn, monkeypatch):
    """进 CSP 的必须是 `scheme://host`，**不是**整条 DSN —— 后者带着 public key，
    拼进响应头既没用（CSP 不认）又白白把凭据摊在每个响应上。"""
    monkeypatch.setenv(SENTRY_FRONTEND_DSN_ENV, DSN)

    assert sentry_origin() == ORIGIN
    assert "public-probe" not in sentry_origin(), sentry_origin()


@pytest.mark.parametrize(
    "value",
    ["", "   ", "not-a-url", "https://", "https://public-probe@", "errors.example.test/7"],
)
def test_origin_is_none_when_there_is_nothing_trustworthy(no_dsn, monkeypatch, value):
    """拿不准就 `None`（fail-closed：不加宽 CSP）。`not-a-url` 那条尤其要挡住 ——
    `urlsplit("not-a-url").netloc` 是空串，去掉校验会拼出 `://` 这种半截值进 CSP。"""
    monkeypatch.setenv(SENTRY_FRONTEND_DSN_ENV, value)

    assert sentry_origin() is None, sentry_origin()


def test_dsn_reader_strips_surrounding_whitespace(no_dsn, monkeypatch):
    """部署侧下发的值带个尾空格是常事（`echo` 进文件、复制粘贴），不该因此整个失效。"""
    monkeypatch.setenv(SENTRY_FRONTEND_DSN_ENV, f"  {DSN}\n")

    assert sentry_frontend_dsn() == DSN


# ═══════════════════════════════════════════════════════════════════════════════
# 接口 + CSP：同一个来源
# ═══════════════════════════════════════════════════════════════════════════════

def test_without_a_dsn_the_endpoint_is_empty_and_the_csp_is_untouched(no_dsn, client):
    """没配置上报 → 接口给 `{}`，CSP 与接线前**逐字一致**。"""
    r = client.get(PATH)

    assert r.status_code == 200, r.text
    assert r.json() == {}, "没配置 DSN 却给了半份配置 —— 前端得去猜它算不算配好了"
    assert CSP_BASE_CONNECT_SRC in _csp(r), _csp(r)
    assert "sentry" not in _csp(r).lower(), _csp(r)


def test_with_a_dsn_the_endpoint_serves_it_and_the_csp_admits_its_origin(no_dsn, monkeypatch, client):
    """配置了 → 接口给 DSN（含 public key，前端要的就是整条），CSP 放行它的 origin。

    两条一起断言是**本文件的重点**：只测接口的话，「接口给了 DSN、CSP 还放着旧的」这种
    半通状态照样全绿，而那时浏览器会把上报请求挡在门外，服务端一条都收不到。
    """
    monkeypatch.setenv(SENTRY_FRONTEND_DSN_ENV, DSN)

    r = client.get(PATH)

    assert r.status_code == 200, r.text
    assert r.json()["sentry_dsn"] == DSN, r.json()
    assert r.json()["region"] == node_region(), r.json()
    assert f"{CSP_BASE_CONNECT_SRC} {ORIGIN};" in _csp(r), _csp(r)


def test_release_is_served_from_the_same_env_var_the_backend_uses(no_dsn, monkeypatch, client):
    """`release` 读 `SENTRY_RELEASE`（后端 SDK 自己也是读它），未下发时**不出现**。"""
    monkeypatch.setenv(SENTRY_FRONTEND_DSN_ENV, DSN)

    assert "release" not in client.get(PATH).json(), "没下发版本却填了个空串"

    monkeypatch.setenv(SENTRY_RELEASE_ENV, "probe-sha-abc123")

    assert client.get(PATH).json()["release"] == "probe-sha-abc123"


def test_reachable_without_a_token(no_dsn, client):
    """前端**登录前**就要取它。"""
    r = client.get(PATH)

    assert r.status_code == 200, r.text
    assert r.json() == {}, r.text


def test_reachable_as_a_demo_account(no_dsn, client, guest_headers):
    """演示账号照样读得到。门禁第 1 步「只读方法先放行」盖住 GET，这条钉住它。

    先钉住「这份凭据**真的**解出了一个用户」：本路径是公开的，随便一个编造的 token 也
    会 200（身份可选），故单看那个 200 说明不了身份被认出来过。判据换成一条**要身份**的
    GET —— 同一份头在那儿 200、不带头 401，才证明这枚 token 是真凭据（`users.role` 是
    本文件自己写进库的 guest），下面那两条断言才不是在测空气。
    """
    protected = client.get("/api/cards/trash", headers=guest_headers)
    assert protected.status_code == 200, (
        f"这份 guest 凭据没被中间件认出来，下面的 200 与演示账号无关："
        f"{protected.status_code} {protected.text}")
    assert client.get("/api/cards/trash").status_code == 401, "无凭据竟然过了 —— 上面那条判据的前提不成立"

    r = client.get(PATH, headers=guest_headers)

    assert r.status_code == 200, f"演示账号被挡在了门外：{r.status_code} {r.text}"
    assert r.json() == {}, r.text
