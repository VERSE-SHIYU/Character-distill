"""API-level test: login→refresh token chain (in-process, no live server).

Verifies:
1. Login returns refresh_token as string (not tuple from unpack bug)
2. Refresh endpoint accepts that string and returns 200
3. New refresh_token is also a string and differs from old token
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from deps import get_storage
from storage.sqlite_store import SQLiteStore

# Patch slowapi rate-limiter before importing the auth router so that
# @limiter.limit(...) decorators become no-ops in tests.
import limiter as _lim_  # noqa: E402  (web/limiter.py)
_lim_.limiter.limit = lambda *a, **kw: lambda f: f

from routers.auth import get_jwt_secret, jwt_secret_source, router as auth_router  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / f"test_{uuid.uuid4().hex}.db")
    return SQLiteStore(db_path)


@pytest.fixture
def app(store):
    _app = FastAPI()
    _app.include_router(auth_router)
    _app.dependency_overrides[get_storage] = lambda: store
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user(store, username: str, password: str) -> dict:
    """Directly create a user in the store (bypasses register endpoint)."""
    uid = f"usr_{uuid.uuid4().hex[:16]}"
    pw_hash = PasswordHash.recommended().hash(password)
    return store.create_user(uid, username, pw_hash)


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestLoginRefreshChain:
    """Login with a store-created user, then refresh the token."""

    def test_login_returns_string_refresh_token(self, store, client):
        """Login response must have refresh_token as a plain string."""
        uid = f"tester_{uuid.uuid4().hex[:8]}"
        pwd = "Pass1234"
        _run(_create_user(store, uid, pwd))

        resp = client.post("/api/auth/login", json={
            "username": uid,
            "password": pwd,
        })
        assert resp.status_code == 200, f"Login failed: {resp.json()}"
        body = resp.json()
        rt = body.get("refresh_token")

        assert isinstance(rt, str), (
            f"refresh_token must be str, got {type(rt).__name__}: {rt}"
        )
        assert len(rt) > 20, f"refresh_token too short"

    def test_refresh_returns_new_string_token(self, store, client):
        """Calling /api/auth/refresh with a valid token must return 200
        and a new string token different from the old one."""
        uid = f"tester_{uuid.uuid4().hex[:8]}"
        pwd = "Pass1234"
        _run(_create_user(store, uid, pwd))

        # Login
        login_resp = client.post("/api/auth/login", json={
            "username": uid,
            "password": pwd,
        })
        assert login_resp.status_code == 200
        rt = login_resp.json()["refresh_token"]
        assert isinstance(rt, str)

        # Refresh
        refresh_resp = client.post("/api/auth/refresh", json={
            "refresh_token": rt,
        })
        assert refresh_resp.status_code == 200, (
            f"Refresh failed: {refresh_resp.json()}"
        )
        body2 = refresh_resp.json()
        rt2 = body2.get("refresh_token")

        assert isinstance(rt2, str), (
            f"refreshed token must be str, got {type(rt2).__name__}: {rt2}"
        )
        assert rt2 != rt, "refresh must rotate the token"
        assert len(rt2) > 20, f"refreshed token too short"


# ── 注入点（缺陷 46）───────────────────────────────────────────────────────────


def test_secret_is_injected_not_read_from_environment(store):
    """secret 的两个注入点都要被覆盖：签发侧的 `get_jwt_secret`、验签侧的
    `jwt_secret_source`（缺陷 95 后的分工 —— 后者给的是取值函数，不是值）。

    覆盖值刻意**不等于** `conftest.py` 注入的那个环境值。于是任何一侧（`login` 签发 /
    `get_current_user` 验签）只要回去读环境，`/me` 就会拿另一个 secret 验签而 401。
    这条红 = 「secret 不再从依赖来」，失败信息里直接写清该看哪儿。
    """
    injected = "injected-" + uuid.uuid4().hex + "-0123456789abcdef"
    _app = FastAPI()
    _app.include_router(auth_router)
    _app.dependency_overrides[get_storage] = lambda: store
    _app.dependency_overrides[get_jwt_secret] = lambda: injected
    # 验签侧收的是**取值函数**：给值时再包一层，别把 injected 当函数交出去。
    _app.dependency_overrides[jwt_secret_source] = lambda: (lambda: injected)
    c = TestClient(_app)

    uid = f"tester_{uuid.uuid4().hex[:8]}"
    pwd = "Pass1234"
    _run(_create_user(store, uid, pwd))

    login = c.post("/api/auth/login", json={"username": uid, "password": pwd})
    assert login.status_code == 200, login.text
    me = c.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.status_code == 200, (
        f"/me 验签失败 —— secret 没有从 `Depends(get_jwt_secret)` 来"
        f"（签发侧或验签侧回去读了环境）：{me.text}"
    )
    assert me.json()["username"] == uid
