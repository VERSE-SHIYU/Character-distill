# -*- coding: utf-8 -*-
"""缺陷 96 步骤 3 的锁：时区在**请求入口统一确定**，业务代码不再各处塞。

**为什么不带时区的请求会「永远上海」。** 时区原先只活在引擎内存里（`client_tz` 由前端
随请求体传），请求入口一无所知 —— 群聊路径压根没接过这个参数。把「本次请求该用哪个
时区」提到入口（按 GitHub 的口径：`Time-Zone` 头 → 用户已存时区 → 不设），业务侧就只剩
一个读口（`UserClock`），派生面（后台线程）也自动带上。

**观测点为什么是探针路由而不是测试线程。** ContextVar 设在 ASGI 栈里，测试线程读不到
`_current_timezone` —— 那是另一份上下文，读到的永远是空值，用例会假绿。所以真
`AuthMiddleware` 挂在一个探针 app 上（中间件只活在 ASGI 栈上，另搭一个 app 测不到生产那
一份），由路由体在**请求内**读 `UserClock.now()` 并回显。库值则用独立连接轮询 —— 中间件
的写是 `ensure_future` 的 fire-and-forget，响应回来时它可能还没落库。

**CORS 那条为什么断言配置而不是真预检。** 真 OPTIONS 预检在这套栈上拿到的是 401：栈序是
「后加的在外」，`AuthMiddleware` 在 `CORSMiddleware` 外面，无凭据的预检根本走不到 CORS
（实测，见 `test_cors_allows_the_time_zone_header`）。所以能观测到的唯一真相就是那份
allowlist 本身 —— 断言它，并说明为什么不是预检。

**边界**：锁的是「入口定出什么时区」与「什么时候不写库」。不锁 `users.timezone` 的列形态
（那是步骤 1 的 `test_postgres_store.py::TestUserCrud::test_timezone_column_roundtrips`），
不锁 `UserClock` 自己的回退（那是 `test_clock.py`）。

Run: pytest tests/test_request_timezone.py -v
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import deps
import server
from core.clock import DEFAULT_TZ, UserClock
from routers.auth import JWT_ALGORITHM, _create_access_token, get_jwt_secret
from storage.sqlite_store import SQLiteStore

#: 探针路由：受保护（`/api/` 开头且不在 `PUBLIC_PREFIXES` 里）→ 中间件会解析身份。
PROBE = "/api/tz-probe"
#: 同一个探针的公开路径版本：`/api/market/` 是 `PUBLIC_PREFIXES` 里的轮询路径。
PROBE_PUBLIC = "/api/market/tz-probe"

SYDNEY = "Australia/Sydney"
TOKYO = "Asia/Tokyo"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _token(user: dict) -> str:
    return _create_access_token(user["id"], user["username"], get_jwt_secret())


def _bearer(user: dict, tz: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token(user)}"}
    if tz is not None:
        headers["Time-Zone"] = tz
    return headers


# ── 夹具 ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "request_tz.db"


@pytest.fixture
def store(db_path):
    return SQLiteStore(str(db_path))


@pytest.fixture
def user(store):
    return _run(store.create_user(
        f"usr_{uuid.uuid4().hex[:16]}",
        "Tz_" + uuid.uuid4().hex[:8],
        "probe-hash",   # 本文件不登录，口令散列不值当算
    ))


def _build_app() -> FastAPI:
    """真 `AuthMiddleware` + 三条探针路由。

    路由体在**请求内**读时钟：测试线程读不到中间件设的 ContextVar，回显是唯一的观测面。
    """
    app = FastAPI()

    def _zone_now() -> str:
        return str(UserClock.now().tzinfo)

    @app.get(PROBE)
    def _probe():
        return {"zone": _zone_now()}

    @app.get(PROBE + "/thread")
    async def _probe_thread():
        """派生面：`asyncio.to_thread` 里还读不读得到请求时区。"""
        return {"zone": await asyncio.to_thread(_zone_now)}

    @app.get(PROBE_PUBLIC)
    def _probe_public():
        return {"zone": _zone_now()}

    app.add_middleware(server.AuthMiddleware)
    return app


@pytest.fixture
def client(store, monkeypatch):
    """`with` 是必须的：不用上下文管理器时每个请求单开一个事件循环并在响应后关掉，
    中间件 `ensure_future` 出来的那次落库会被一起丢掉。"""
    monkeypatch.setattr(deps, "_storage", store)
    with TestClient(_build_app(), raise_server_exceptions=False) as c:
        yield c
        # 最后一次请求 ensure_future 出去的后台写（last_active / 时区）若在关循环时还挂着，
        # 退场会变成 PytestUnhandledThreadExceptionWarning —— 给它们一拍跑完再关。
        time.sleep(0.5)


# ── 库值观测（独立连接，绕开 store 的事件循环亲和）──────────────────────────


def _stored_tz(db_path, user_id: str) -> str | None:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute("SELECT timezone FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def _wait_for_tz(db_path, user_id: str, expected: str, timeout: float = 10.0):
    """轮询等 fire-and-forget 的写落库 —— 响应回来时它可能还没跑。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = _stored_tz(db_path, user_id)
        if got == expected:
            return got
        time.sleep(0.1)
    return _stored_tz(db_path, user_id)


def _settle(seconds: float = 0.6) -> None:
    """给「不该发生的写」一点时间去发生 —— 否则空缺的断言只是跑得快。"""
    time.sleep(seconds)


# ── 1. 合法请求头：本次请求用它，并落库 ──────────────────────────────────────


def test_valid_header_reaches_the_clock(client, db_path, user):
    r = client.get(PROBE, headers=_bearer(user, SYDNEY))
    assert r.status_code == 200, f"探针没跑起来：{r.status_code} {r.text[:200]}"
    assert r.json()["zone"] == SYDNEY, (
        f"请求头里的时区没进本次请求的时钟，读到 {r.json()['zone']}")


def test_valid_header_is_persisted(client, db_path, user):
    client.get(PROBE, headers=_bearer(user, SYDNEY))
    got = _wait_for_tz(db_path, user["id"], SYDNEY)
    assert got == SYDNEY, f"请求头里的时区没落库，库中为 {got!r}"


def test_derived_thread_sees_the_request_timezone(client, user):
    """好感度评估跑在 `asyncio.to_thread` 里 —— 它必须和请求同一个时区。"""
    r = client.get(PROBE + "/thread", headers=_bearer(user, TOKYO))
    assert r.status_code == 200, f"探针没跑起来：{r.status_code} {r.text[:200]}"
    assert r.json()["zone"] == TOKYO, (
        f"派生线程读到的时区是 {r.json()['zone']}，不是请求的 {TOKYO}")


# ── 2. 不带头：用已存时区；都没有 → 回退 ─────────────────────────────────────


def test_stored_timezone_is_used_without_header(client, user, store):
    _run(store.update_user_timezone(user["id"], SYDNEY))
    r = client.get(PROBE, headers=_bearer(user))
    assert r.json()["zone"] == SYDNEY, (
        f"没带头时应回落到已存时区 {SYDNEY}，读到 {r.json()['zone']}")


def test_falls_back_to_default_when_nothing_is_known(client, user):
    r = client.get(PROBE, headers=_bearer(user))
    assert r.json()["zone"] == DEFAULT_TZ, (
        f"头与库都没有时应回退 {DEFAULT_TZ}，读到 {r.json()['zone']}")


def test_header_beats_stored_timezone(client, user, store):
    _run(store.update_user_timezone(user["id"], SYDNEY))
    r = client.get(PROBE, headers=_bearer(user, TOKYO))
    assert r.json()["zone"] == TOKYO, (
        f"请求头应压过已存值，读到 {r.json()['zone']}")


# ── 3. 非法时区名：忽略，且不写库 ────────────────────────────────────────────


def test_invalid_header_is_ignored_and_not_persisted(client, db_path, user):
    r = client.get(PROBE, headers=_bearer(user, "Mars/Base"))
    assert r.status_code == 200, f"探针没跑起来：{r.status_code} {r.text[:200]}"
    assert r.json()["zone"] == DEFAULT_TZ, (
        f"非法时区名应被忽略、回退 {DEFAULT_TZ}，读到 {r.json()['zone']}")
    _settle()
    got = _stored_tz(db_path, user["id"])
    assert got == "", f"非法时区名被写进了库：{got!r}"


def test_invalid_stored_timezone_is_ignored(client, user, store):
    """库里的值可能是旧版本或手改的脏数据 —— 它同样要被 `ZoneInfo` 挡一次。"""
    _run(store.update_user_timezone(user["id"], "Mars/Base"))
    r = client.get(PROBE, headers=_bearer(user))
    assert r.json()["zone"] == DEFAULT_TZ, (
        f"库里的非法时区名应回退 {DEFAULT_TZ}，读到 {r.json()['zone']}")


# ── 4. 公开路径：认得出身份也不写库 ──────────────────────────────────────────


def test_public_path_does_not_write(client, db_path, user):
    r = client.get(PROBE_PUBLIC, headers=_bearer(user, SYDNEY))
    assert r.status_code == 200, f"探针没跑起来：{r.status_code} {r.text[:200]}"
    assert r.json()["zone"] == SYDNEY, "公开路径也该用请求头里的时区"
    _settle()
    got = _stored_tz(db_path, user["id"])
    assert got == "", f"公开路径不该写库（那是轮询路径，写放大换不到东西），却写成 {got!r}"


# ── 5. 前端要发得出的那个头，CORS 得放行 ────────────────────────────────────


def test_cors_allows_the_time_zone_header():
    """断言 allowlist 而不是真预检：`AuthMiddleware` 在 `CORSMiddleware` 外面，无凭据的
    OPTIONS 预检先被它 401 掉（实测），走不到 CORS —— 那份 allowlist 是唯一可观测的真相。"""
    allowed: list[str] | None = None
    for m in server.app.user_middleware:
        if m.cls is CORSMiddleware:
            allowed = list(m.kwargs.get("allow_headers") or [])
    assert allowed is not None, "server.app 上没有 CORSMiddleware —— 机制没了"
    assert "Time-Zone" in allowed, (
        f"CORS allow_headers 不含 Time-Zone（{allowed}）—— 跨域预检会拦下这个头")
