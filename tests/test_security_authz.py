"""Security authorization integration tests.

Tests two security invariants:
1. Resource owner isolation: user B cannot access user A's resources (gets 403)
2. Error response safety: system exceptions -> sanitized 500; ValueError -> 400

Run: pytest tests/test_security_authz.py -v
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from deps import get_storage
from routers.auth import get_current_user
from routers.card import router as card_router
from routers.history import router as history_router
from routers.group import router as group_router
from routers.chat import router as chat_router
from storage.sqlite_store import SQLiteStore


def _run_async(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / f"test_{uuid.uuid4().hex}.db")
    return SQLiteStore(db_path)


@pytest.fixture
def user_a():
    return f"user_a_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def user_b():
    return f"user_b_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def user_c():
    """Used for 'not found' / non-existent tests."""
    return f"user_c_{uuid.uuid4().hex[:8]}"


# ── App factory ──

def _make_app(store, user_id, *, include_error_handler=True):
    """Build a minimal FastAPI app with the routers and dependency overrides."""
    app = FastAPI()
    app.include_router(card_router)
    app.include_router(history_router)
    app.include_router(group_router)
    app.include_router(chat_router)

    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "username": "testuser",
        "is_admin": False,
    }

    if include_error_handler:
        @app.exception_handler(Exception)
        async def _global_exc_handler(request: Request, exc: Exception):
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"detail": "服务器内部错误，请稍后重试"},
            )

    return app


@pytest.fixture
def app_a(store, user_a):
    return _make_app(store, user_a)


@pytest.fixture
def app_b(store, user_b):
    return _make_app(store, user_b)


@pytest.fixture
def app_c(store, user_c):
    """No error handler on this app — tests error handler registration."""
    return _make_app(store, user_c, include_error_handler=False)


@pytest.fixture
def client_a(app_a):
    return TestClient(app_a)


@pytest.fixture
def client_b(app_b):
    return TestClient(app_b)


@pytest.fixture
def client_c(app_c):
    return TestClient(app_c)


@pytest.fixture
def client_a_no_raise(app_a):
    """TestClient with raise_server_exceptions=False — for error-sanitization tests that trigger ASGI exceptions."""
    return TestClient(app_a, raise_server_exceptions=False)


@pytest.fixture
def client_c_no_raise(app_c):
    """TestClient without error handler AND without exception re-raise."""
    return TestClient(app_c, raise_server_exceptions=False)


# ── Test data helpers ─────────────────────────────────────────────────────────

def _create_text(store, user_id):
    text_id = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(text_id, "src.txt", "content", user_id=user_id))
    return text_id


def _create_card(store, user_id, text_id):
    card_id = f"card_{uuid.uuid4().hex}"
    _run_async(store.save_card(card_id, text_id, "张三", '{"name": "张三"}', user_id=user_id))
    return card_id


def _create_session(store, user_id, card_id):
    session_id = f"ses_{uuid.uuid4().hex}"
    _run_async(store.save_session(session_id, card_id, "user", "", user_id=user_id))
    return session_id


def _create_group(store, user_id):
    group_id = f"grp_{uuid.uuid4().hex}"
    _run_async(store.create_group_session(group_id, "群聊A", [], user_id=user_id))
    return group_id


# ═══════════════════════════════════════════════════════════════════════════════
# Authorization — User B cannot access User A's resources
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadAuthorization:
    """Each endpoint: user B → user A resource → 403."""

    def test_01_history_session_403(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        r = client_b.get(f"/api/history/{sid}")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.json()}"

    def test_02_history_export_403(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        r = client_b.get(f"/api/history/{sid}/export")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.json()}"

    def test_03_card_get_403(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        r = client_b.get(f"/api/cards/{cid}")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.json()}"

    def test_04_card_export_403(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        r = client_b.get(f"/api/cards/{cid}/export")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.json()}"

    def test_05_group_history_403(self, store, user_a, client_b):
        gid = _create_group(store, user_a)
        r = client_b.get(f"/api/group/{gid}/history")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.json()}"

    def test_06_chat_affinity_403(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        r = client_b.get(f"/api/chat/affinity/{sid}")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.json()}"


# ═══════════════════════════════════════════════════════════════════════════════
# Control: User A can access own resources; nonexistent IDs return 404
# ═══════════════════════════════════════════════════════════════════════════════

class TestControlAccess:
    """Sanity checks: 403 tests shouldn't break legitimate access."""

    def test_07a_own_history_200(self, store, user_a, client_a):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        r = client_a.get(f"/api/history/{sid}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.json()}"
        assert "messages" in r.json()

    def test_07b_own_card_200(self, store, user_a, client_a):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        r = client_a.get(f"/api/cards/{cid}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.json()}"
        assert "name" in r.json()

    def test_07c_own_card_export_200(self, store, user_a, client_a):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        r = client_a.get(f"/api/cards/{cid}/export")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        # export returns a file download (not JSON)
        assert "attachment" in r.headers.get("content-disposition", "")

    def test_08a_history_not_found_404(self, store, client_a):
        r = client_a.get(f"/api/history/nonexistent_{uuid.uuid4().hex}")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_08b_card_not_found_404(self, store, client_a):
        r = client_a.get(f"/api/cards/nonexistent_{uuid.uuid4().hex}")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_08c_group_not_found_404(self, store, client_a):
        r = client_a.get(f"/api/group/nonexistent_{uuid.uuid4().hex}/history")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_08d_affinity_not_found_404(self, store, client_a):
        r = client_a.get(f"/api/chat/affinity/nonexistent_{uuid.uuid4().hex}")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"


# ═══════════════════════════════════════════════════════════════════════════════
# Error leakage: system exceptions don't leak internals
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorSanitization:
    """System exceptions → sanitized 500; ValueError → 400."""

    def test_09_system_exception_no_leak(self, store, user_a, client_a_no_raise, monkeypatch):
        """When storage raises RuntimeError, response must not leak internals.

        Uses client_a_no_raise (raise_server_exceptions=False) because Starlette's
        ServerErrorMiddleware re-raises after sending the 500 response, and the
        default TestClient propagates the re-raise instead of returning it.
        """
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)

        async def _broken(*args, **kwargs):
            raise RuntimeError("秘密密码: postgres://user:pass@prod-db:5432/charsim")

        monkeypatch.setattr(store, "get_card", _broken)
        r = client_a_no_raise.get(f"/api/cards/{cid}")
        assert r.status_code == 500, f"Expected 500, got {r.status_code}: {r.json()}"
        detail = r.json().get("detail", "")
        # Must NOT leak internals
        assert "秘密密码" not in detail, f"Leaked secret: {detail}"
        assert "Traceback" not in detail, f"Leaked traceback: {detail}"
        assert "postgres" not in detail, f"Leaked SQL/db info: {detail}"
        assert "RuntimeError" not in detail, f"Leaked exception type: {detail}"
        # Must return a generic safe message
        assert "服务器内部错误" in detail, f"Unexpected message: {detail}"

    def test_09b_system_exception_without_handler_500(self, store, user_a, client_c_no_raise, monkeypatch):
        """Without the global error handler, the server still returns 500 (not crash).

        client_c has no @exception_handler(Exception), so Starlette returns its
        default 500 with 'Internal Server Error' (plain text) — still safe.
        """
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)

        async def _broken(*args, **kwargs):
            raise RuntimeError("内部爆炸")

        monkeypatch.setattr(store, "get_card", _broken)
        r = client_c_no_raise.get(f"/api/cards/{cid}")
        assert r.status_code == 500, f"Expected 500, got {r.status_code}"
        text = r.text
        assert "Traceback" not in text, f"Leaked traceback: {text}"
        assert "RuntimeError" not in text, f"Leaked exception type: {text}"
        # Starlette's plain-text default is also safe
        assert "Internal Server Error" in text, f"Unexpected body: {text}"

    def test_10_value_error_400(self, store, user_a, client_a, monkeypatch):
        """ValueError from storage should surface as 400 with friendly message."""
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)

        async def _broken(*args, **kwargs):
            raise ValueError("不支持的导出格式: xlsx")

        monkeypatch.setattr(store, "export_session", _broken)
        r = client_a.get(f"/api/history/{sid}/export")
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.json()}"
        detail = r.json().get("detail", "")
        # ValueError message should be visible (it's a friendly business error)
        assert "xlsx" in detail, f"ValueError message not surfaced: {detail}"
