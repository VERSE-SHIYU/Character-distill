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

from routers.auth import router as auth_router  # noqa: E402


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
