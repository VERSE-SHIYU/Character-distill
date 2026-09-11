"""Security authorization integration tests.

Tests two security invariants:
1. Resource owner isolation: user B cannot access user A's resources -> 404，不是 403。
   403 说「资源存在但你没权限」，泄漏存在性；404 让「非属主」与「不存在」不可区分，
   否则攻击者拿一批 id 扫描时，状态码差异就是存在性枚举的预言机。理由详见
   TestReadAuthorization 的文档串与 AGENTS.md §四。
2. Error response safety: system exceptions -> sanitized 500; ValueError -> 400

注：本文件里 test_03/04/05（card、group）仍断言 403，因为那批端点还没翻新——
那是存量口径问题，不是有意保留的第二种语义，见 AGENTS.md 缺陷 13。

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
from routers.distill import router as distill_router
from storage.sqlite_store import SQLiteStore


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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
    app.include_router(distill_router)

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
    """Each endpoint: user B → user A resource → 404(不可区分于不存在)。

    为什么是 404 不是 403：403 说「资源存在但你没权限」，泄漏存在性；404 让「非属主」
    与「不存在」不可区分。攻击者拿一批 id 去扫时，403/404 的差异就是枚举预言机。
    这些端点的属主过滤在 storage 的 *_owned 原语里用 SQL 完成，拿不到行即 404。
    不要改回 403 —— 那会重新开一个存在性枚举点。
    """

    def test_01_history_session_404(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        r = client_b.get(f"/api/history/{sid}")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_02_history_export_404(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        r = client_b.get(f"/api/history/{sid}/export")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

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

    def test_06_chat_affinity_404(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        r = client_b.get(f"/api/chat/affinity/{sid}")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: the 7 ownership holes closed by the *_owned capability split
# ═══════════════════════════════════════════════════════════════════════════════

class _StubTextManager:
    """最小可用的 TextManager 替身：让 start_session 能走完建会话那几步。

    存在的理由：属主门若被去掉，start_session 会真的建出会话并返回 200。只有让流程能走完，
    test_17 才不是一条恒绿用例——否则它在门被去掉时也只是换了个异常。
    """

    async def _build_all_characters(self, *args, **kwargs):
        return []

    def _create_session(self, *args, **kwargs):
        return f"ses_stub_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def llm_gate_open(store, monkeypatch):
    """打开端点的 503「未配置 API Key」前置门，让请求能走到属主门。

    这批端点在读文本前先判 distiller / text_manager 是否为 None，无 API Key 时直接 503，
    不打开这道门就测不到属主过滤。只替换 deps 的工厂，不碰被测的属主逻辑。

    另隔离一个无关缺陷：storage/migrations/067_embedding_config.sql 用
    `ADD COLUMN IF NOT EXISTS`（PostgreSQL 语法，SQLite 不支持），迁移静默失败，
    于是新建的 SQLite 库缺 users.embedding_key，get_user_api_config 抛
    OperationalError → 500，挡在属主门之前。这里让该读返回空配置，使用例只测属主过滤。
    """
    import deps
    import web.routers.distill as distill_mod

    async def _fake_user_llm(*args, **kwargs):
        return object()

    async def _fake_api_config(*args, **kwargs):
        return {}

    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_distiller", lambda *a, **kw: object())
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: _StubTextManager())
    monkeypatch.setattr(store, "get_user_api_config", _fake_api_config)
    # start_session 建完会话会顺手排场景索引；那条路要用真实 storage，测试里关掉。
    monkeypatch.setattr(distill_mod, "get_indexing_service", lambda: None)


class TestHoleOwnershipRegression:
    """每个洞一条：非属主拿他人 text_id / session_id 请求 → 404。

    为什么这些用例有效：属主门拿不到行才 404；一旦门被去掉（改回 *_unscoped 或 SQL 里
    丢掉 user_id 条件），请求会继续往下走，状态码不再可能是 404，用例即红。所以「404」
    本身就是鉴别力，不需要另设属主正向用例。

    两条 session 用例额外断言 _sessions 里没有被写入他人会话——resume_session 的越权
    后果最重：它会把为受害者重建的 ChatEngine 塞进共享 dict（注释原文 "Steal the engine"）。
    """

    def test_11_resume_session_non_owner_404(self, store, user_a, user_b, client_b, llm_gate_open):
        """夹具刻意让 A 的 card 指向 **B 的** text。

        resume_session 现在是双门：session 属主门 + 下游 get_text_owned。若夹具让 A 的 card
        指向 A 的 text，则只把 session 门改回 *_unscoped 时下游门会兜住，用例恒绿、测不出
        session 门被去掉。把下游门让开，这条用例才真正锁住 session 那道门。
        """
        from deps import get_sessions
        tid_b = _create_text(store, user_b)
        cid = _create_card(store, user_a, tid_b)
        sid = _create_session(store, user_a, cid)
        r = client_b.post(f"/api/history/{sid}/resume", json={})
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"
        assert sid not in get_sessions(), "非属主在 _sessions 里为他人会话重建了引擎"

    def test_12_distill_identify_non_owner_404(self, store, user_a, client_b, llm_gate_open):
        tid = _create_text(store, user_a)
        r = client_b.post("/api/distill/identify", json={"text_id": tid})
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_13_distill_run_non_owner_404(self, store, user_a, client_b, llm_gate_open):
        tid = _create_text(store, user_a)
        r = client_b.post("/api/distill/run", json={"text_id": tid})
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_14_distill_start_non_owner_404(self, store, user_a, client_b, llm_gate_open):
        tid = _create_text(store, user_a)
        r = client_b.post("/api/distill/start", json={"text_id": tid})
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_15_distill_run_stream_non_owner_404(self, store, user_a, client_b, llm_gate_open):
        tid = _create_text(store, user_a)
        r = client_b.post("/api/distill/run_stream", json={"text_id": tid})
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_16_distill_reindex_non_owner_404(self, store, user_a, client_b, llm_gate_open):
        tid = _create_text(store, user_a)
        r = client_b.post(f"/api/distill/reindex/{tid}")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"

    def test_17_distill_start_session_non_owner_404(self, store, user_a, client_b, llm_gate_open):
        """属主门在 try 内，其 404 一度被同块的宽 except 吞成 500。

        这条用例同时锁两件事：状态码是 404（宽 except 前必须 `except HTTPException: raise`），
        且没有为别人建成会话。只锁「不建成会话」是不够的——500 满足它，却把 4xx 的客户端
        条件报成服务端错误，与 §四 的 404 口径冲突。
        """
        from deps import get_sessions
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        before = len(get_sessions())
        r = client_b.post("/api/distill/start_session", json={"card_id": cid, "text_id": tid})
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"
        assert len(get_sessions()) == before, "非属主用他人 text_id 建出了会话并写进 _sessions"

    def test_18_chat_revoke_non_owner_404(self, store, user_a, user_b, client_b, llm_gate_open):
        """第 7 个洞：_ensure_session 的 DB 重建分支缺属主校验（内存命中分支有）。

        同 test_11：夹具让 A 的 card 指向 B 的 text，让开下游 get_text_owned，这条用例才
        真正锁住 session 那道门。
        """
        from deps import get_sessions
        tid_b = _create_text(store, user_b)
        cid = _create_card(store, user_a, tid_b)
        sid = _create_session(store, user_a, cid)
        r = client_b.post("/api/chat/revoke", json={"session_id": sid, "message_id": 1})
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"
        assert sid not in get_sessions(), "非属主在 _sessions 里为他人会话重建了引擎"

    def test_19_chat_memory_hit_non_owner_404(self, store, user_a, client_b):
        """内存命中分支与 DB 重建分支同判 404（含同一条文案）。

        同一个 session_id，内存里有没有这条记录会走 `_ensure_session` 的不同分支。两条分支的
        状态码若不同，攻击者反复请求、靠命中/未命中的差异就能推断资源是否存在——内存路径会
        把 DB 路径的防枚举漏掉。
        """
        from deps import get_sessions
        sid = f"ses_mem_{uuid.uuid4().hex}"
        get_sessions()[sid] = {"user_id": user_a, "engine": object()}
        try:
            r = client_b.post("/api/chat/revoke", json={"session_id": sid, "message_id": 1})
        finally:
            get_sessions().pop(sid, None)
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"


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


# ── FERNET_KEY format validation ──────────────────────────────────────────


class TestFernetKeyValidation:
    """Startup validation: bad FERNET_KEY format must be caught early."""

    def test_hex_key_raises(self, monkeypatch):
        """Hex-encoded 32-byte key (the SZ bug) must raise RuntimeError."""
        monkeypatch.setenv("FERNET_KEY", "9b71" + "a" * 60)
        from routers.auth import validate_fernet_key
        with pytest.raises(RuntimeError, match="FERNET_KEY 格式错误"):
            validate_fernet_key()

    def test_valid_base64_key_passes(self, monkeypatch):
        """Valid url-safe base64 key must pass without error."""
        from cryptography.fernet import Fernet
        monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
        from routers.auth import validate_fernet_key
        validate_fernet_key()  # should not raise

    def test_no_key_passes(self, monkeypatch):
        """Missing FERNET_KEY must pass (JWT_SECRET fallback valid separately)."""
        monkeypatch.delenv("FERNET_KEY", raising=False)
        from routers.auth import validate_fernet_key
        validate_fernet_key()  # should not raise
