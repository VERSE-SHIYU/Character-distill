"""Security authorization integration tests.

Tests two security invariants:
1. Resource owner isolation: user B cannot access user A's resources -> 404，不是 403。
   403 说「资源存在但你没权限」，泄漏存在性；404 让「非属主」与「不存在」不可区分，
   否则攻击者拿一批 id 扫描时，状态码差异就是存在性枚举的预言机。理由详见
   TestReadAuthorization 的文档串与 AGENTS.md §四。
2. Error response safety: system exceptions -> sanitized 500; ValueError -> 400

注：全仓存量属主型 403 已统一为 404（card / group / market / memory / message / voice /
distill 共 32 处），本文件 test_03/04/05 随之从 403 改 404。权限型（非管理员、账号禁用）
与业务门（审核待审、geo 合规）仍保留 403，见 AGENTS.md 缺陷 13。

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
from routers.market import router as market_router
from routers.text import router as text_router
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
    app.include_router(text_router)
    app.include_router(market_router)

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


def _publish_card(store, user_id, text_id):
    """发布私卡 → 落一条 card_versions（历史版本），返回 fork id。

    fork id 是 `card_versions.card_id` 的主键来源，market 的 /versions 端点按它取版本。
    """
    card_id = _create_card(store, user_id, text_id)
    return _run_async(store.publish_card(
        card_id, user_id, "desc", "tag", "v1", '{"name": "张三"}',
    ))


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
    text / session 类端点的属主过滤在 storage 的 *_owned 原语里用 SQL 完成；card /
    group 等无 *_owned 变体的资源在路由层把「不存在」与「非属主」两个分支合并成同一个
    404。两条路径都以「404 与不存在同码同文案」为准。不要改回 403 —— 那会重新开一个
    存在性枚举点。
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

    def test_03_card_get_404(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        r = client_b.get(f"/api/cards/{cid}")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"
        assert r.status_code != 403

    def test_04_card_export_404(self, store, user_a, client_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        r = client_b.get(f"/api/cards/{cid}/export")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"
        assert r.status_code != 403

    def test_05_group_history_404(self, store, user_a, client_b):
        gid = _create_group(store, user_a)
        r = client_b.get(f"/api/group/{gid}/history")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"
        assert r.status_code != 403

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
def llm_gate_open(monkeypatch):
    """打开端点的 503「未配置 API Key」前置门，让请求能走到属主门。

    这批端点在读文本前先判 distiller / text_manager 是否为 None，无 API Key 时直接 503，
    不打开这道门就测不到属主过滤。只替换 deps 的工厂，不碰被测的属主逻辑。
    """
    import deps
    import web.routers.distill as distill_mod

    async def _fake_user_llm(*args, **kwargs):
        return object()

    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_distiller", lambda *a, **kw: object())
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: _StubTextManager())
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
# 缺陷 19：读取原语本身无身份（与上面那批「调用点忘写 if」不同型）
# ═══════════════════════════════════════════════════════════════════════════════

class TestDefect19PrimitiveLeaks:
    """三处实锤越权：读原语无身份概念，调用点漏校验 → 非属主可读。

    与 TestHoleOwnershipRegression 的区别：那批的根因是**某个调用点忘了写 if**，修法是
    在那个调用点补校验；这三条的根因是**原语签名里没有 user**，于是每个调用点都得记得校验，
    忘一个漏一个，而漏了没有报警。修法是原语拆出 `*_owned`（属主过滤在 SQL，复用缺陷 11
    的 `get_text_owned` / `get_session_owned` 范式），调用点无处可选。

    所以这批用例的判据不是「路由返回 404」，而是**原语在 SQL 层就返回空**；路由级用例只
    保证调用点接上了 `*_owned`。

    `test_12` 为什么不能覆盖 `get_characters` 的越权：它建的文本**没有缓存**
    （`characters_json` 为空），旧代码读缓存得 None、照样往下走 404，所以旧代码下它也绿。
    真正的判别力要求**缓存已存在**——见 test_23。这就是「探针自效性」：夹具没走到那条
    分支，用例就是恒绿的。
    """

    def test_20_characters_owned_sql_filter(self, store, user_a, user_b):
        """原语层：非属主读他人文本的角色缓存得 None（属主过滤在 SQL）。"""
        tid = _create_text(store, user_a)
        _run_async(store.save_characters(tid, [{"name": "张三", "aliases": ["三哥"]}]))
        assert _run_async(store.get_characters_owned(tid, user_a)) == [{"name": "张三", "aliases": ["三哥"]}]
        assert _run_async(store.get_characters_owned(tid, user_b)) is None

    def test_21_text_comments_owned_sql_filter(self, store, user_a, user_b):
        """原语层：非属主读他人文本的评论得空页，不是「无权限」也不是别人家的评论。

        属主列在 `texts` 上 —— `text_comments.user_id` 是**评论作者**，用它过滤是错的
        （会把「别人在我文本下的评论」滤掉，同时把「我在别人文本下的评论」漏出来）。
        """
        tid = _create_text(store, user_a)
        _run_async(store.add_text_comment(tid, user_a, "A", "我的评论"))
        mine = _run_async(store.get_text_comments_owned(tid, user_a, 1, 20))
        assert mine["total"] == 1 and mine["comments"][0]["content"] == "我的评论"
        theirs = _run_async(store.get_text_comments_owned(tid, user_b, 1, 20))
        assert theirs == {"comments": [], "total": 0}

    def test_22_card_versions_owned_sql_filter(self, store, user_a, user_b):
        """原语层：非属主读他人卡的版本历史得空列表。

        属主是**卡的属主**，不是版本行的 `user_id`；快照里有卡全文，不能给非属主看。
        """
        tid = _create_text(store, user_a)
        fork_id = _publish_card(store, user_a, tid)
        assert fork_id, "夹具没建出已发布版本，本用例会恒绿"
        mine = _run_async(store.get_card_versions_owned(fork_id, user_a))
        assert len(mine) >= 1
        assert _run_async(store.get_card_versions_owned(fork_id, user_b)) == []

    def test_23_distill_identify_non_owner_404_with_cache(self, store, user_a, client_b, llm_gate_open):
        """路由层：**缓存已存在**时，非属主仍 404 —— 这条才锁得住校验与读缓存的顺序。

        旧代码把 `get_characters` 放在 `get_text_owned` 之前，非属主直接命中别人的缓存并
        返回 200；补上 `*_owned` 后即使顺序写反也读不到，但顺序仍必须是「先校验后读」，
        否则 404 是死代码。两条都锁：状态码 + 不返回他人缓存内容。
        """
        tid = _create_text(store, user_a)
        _run_async(store.save_characters(tid, [{"name": "张三", "aliases": ["三哥"]}]))
        r = client_b.post("/api/distill/identify", json={"text_id": tid})
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.json()}"
        assert "张三" not in r.text, "非属主拿到了他人文本的角色缓存"

    def test_24_text_comments_route_non_owner_sees_nothing(self, store, user_a, client_b):
        """路由层：非属主读他人文本的评论端点，看不到别人的评论。"""
        tid = _create_text(store, user_a)
        _run_async(store.add_text_comment(tid, user_a, "A", "我的评论"))
        r = client_b.get(f"/api/text/{tid}/comments")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.json()}"
        assert r.json()["total"] == 0, f"非属主看到了他人文本的评论：{r.json()}"
        assert "我的评论" not in r.text

    def test_25_card_versions_route_non_owner_sees_nothing(self, store, user_a, client_b):
        """路由层：非属主读他人卡的版本历史，拿不到任何版本（快照含卡全文）。"""
        tid = _create_text(store, user_a)
        fork_id = _publish_card(store, user_a, tid)
        assert fork_id, "夹具没建出已发布版本，本用例会恒绿"
        r = client_b.get(f"/api/market/{fork_id}/versions")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.json()}"
        assert r.json()["versions"] == [], f"非属主拿到了他人卡的版本历史：{r.json()}"


class TestDefect19Commit2OwnedPrimitives:
    """commit 2：B1 其余九个原语补齐 `*_owned`，判据 = **SQL 层就过滤**。

    每条都在真实 SQLiteStore 上跑：非属主（或匿名 None）读不到、属主读得到。把任一
    对应 SQL 里的身份谓词删掉，该用例即红 —— 这就是它的红源。路由级用例（本文件
    test_01~25、test_ownership_404.py）只保证调用点接上了 `*_owned`，锁不住 SQL。
    """

    def test_26_card_owned_sql_filter(self, store, user_a, user_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        assert _run_async(store.get_card_owned(cid, user_a))["id"] == cid
        assert _run_async(store.get_card_owned(cid, user_b)) is None

    def test_27_card_avatar_owned_sql_filter(self, store, user_a, user_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        _run_async(store.save_card_avatar(cid, "QVhBVkFUQVI="))
        assert _run_async(store.get_card_avatar_owned(cid, user_a)) == "QVhBVkFUQVI="
        assert _run_async(store.get_card_avatar_owned(cid, user_b)) is None

    def test_28_session_voice_ref_owned_sql_filter(self, store, user_a, user_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        _run_async(store.update_session_voice_ref(cid, '{"path": "/x.wav"}'))
        assert _run_async(store.get_session_voice_ref_owned(cid, user_a)) == '{"path": "/x.wav"}'
        assert _run_async(store.get_session_voice_ref_owned(cid, user_b)) is None

    def test_29_group_session_owned_sql_filter(self, store, user_a, user_b):
        gid = _create_group(store, user_a)
        assert _run_async(store.get_group_session_owned(gid, user_a))["name"] == "群聊A"
        assert _run_async(store.get_group_session_owned(gid, user_b)) is None

    def test_30_dm_message_owned_is_multiway(self, store, user_a, user_b, user_c):
        """DM 的属主是**收发双方**：会话外第三人读不到。"""
        mid = _run_async(store.send_message(user_a, user_b, "悄悄话"))["id"]
        assert _run_async(store.get_dm_message_owned(mid, user_a)) is not None
        assert _run_async(store.get_dm_message_owned(mid, user_b)) is not None
        assert _run_async(store.get_dm_message_owned(mid, user_c)) is None

    def test_31_distill_task_owned_sql_filter(self, store, user_a, user_b):
        tid = _create_text(store, user_a)
        tsk = f"dt_{uuid.uuid4().hex}"
        _run_async(store.create_distill_task(tsk, user_a, tid))
        assert _run_async(store.get_distill_task_owned(tsk, user_a))["task_id"] == tsk
        assert _run_async(store.get_distill_task_owned(tsk, user_b)) is None

    def test_32_comment_owned_is_multiway(self, store, user_a, user_b, user_c):
        """评论的属主是**评论作者 ∨ 卡作者**（删除权限，见 market.delete_comment）。"""
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        cmid = _run_async(store.add_comment(cid, user_b, "B", "路过"))["id"]
        assert _run_async(store.get_comment_owned(cmid, user_a)) is not None  # 卡作者
        assert _run_async(store.get_comment_owned(cmid, user_b)) is not None  # 评论作者
        assert _run_async(store.get_comment_owned(cmid, user_c)) is None

    def test_33_comments_owned_is_visibility_narrowed(self, store, user_a, user_b):
        """公开列表：公开卡评论任何人可读，私卡评论只给卡主，匿名只命中公开分支。"""
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)  # 默认 private
        _run_async(store.add_comment(cid, user_b, "B", "私卡评论"))
        assert len(_run_async(store.get_comments_owned(cid, user_a))) == 1
        assert _run_async(store.get_comments_owned(cid, user_b)) == []
        assert _run_async(store.get_comments_owned(cid, None)) == []
        _run_async(store.update_card_visibility(cid, "public"))
        assert len(_run_async(store.get_comments_owned(cid, user_b))) == 1
        assert len(_run_async(store.get_comments_owned(cid, None))) == 1

    def test_34_post_comments_owned_is_visibility_narrowed(self, store, user_a, user_b):
        """帖子同范式：私密帖评论只给发帖人，公开帖评论任何人可读。"""
        priv = _run_async(store.add_post(user_a, "私密帖", "private"))["id"]
        pub = _run_async(store.add_post(user_a, "公开帖", "public"))["id"]
        _run_async(store.add_post_comment(priv, user_b, "B", "私帖评论"))
        _run_async(store.add_post_comment(pub, user_b, "B", "公开帖评论"))
        assert len(_run_async(store.get_post_comments_owned(priv, user_a))) == 1
        assert _run_async(store.get_post_comments_owned(priv, user_b)) == []
        assert _run_async(store.get_post_comments_owned(priv, None)) == []
        assert len(_run_async(store.get_post_comments_owned(pub, user_b))) == 1
        assert len(_run_async(store.get_post_comments_owned(pub, None))) == 1


class TestDefect19Commit4B3Verdicts:
    """commit 4：B3 裁决落地 —— 重键之后，锁判据随 SQL 事实一起变。

    旧 `get_reactions(message_ids)` 收一组**调用方自拼的 message_id**，没有任何身份
    谓词；唯一的上界是「调用点碰巧只传本会话的 id」。重键为
    `get_session_reactions_owned(session_id, user_id)`：属主谓词落在 SQL 里
    （JOIN sessions），调用方再拼不出「任意 message_ids」这个越权面。
    删掉那条 SQL 的 `AND s.user_id = ?`，test_35 即红 —— 这就是它的红源。

    test_36 守的是本重键的**理由**：`message_reactions.user_id` 不只是「点赞的人」，
    群里角色反应写成 `char:<card_id>`（group.py 的 toggle_reaction），所以属主过滤
    只能走 JOIN sessions，不能按 mr.user_id 收窄。若有人把它改回 mr.user_id 过滤，
    test_36 即红。
    """

    def test_35_session_reactions_owned_sql_filter(self, store, user_a, user_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        mid = _run_async(store.save_message(sid, "user", "hi", ""))["id"]
        _run_async(store.toggle_reaction(mid, user_a, "👍"))

        owner = _run_async(store.get_session_reactions_owned(sid, user_a))
        assert owner.get(mid), "属主读不到自己会话的反应，本用例会恒绿"
        assert owner[mid][0]["emoji"] == "👍"
        assert _run_async(store.get_session_reactions_owned(sid, user_b)) == {}, \
            "非属主拿到了他人会话的反应"

    def test_36_session_reactions_owned_keeps_char_reactions(self, store, user_a):
        """角色反应（user_id = char:<card_id>）在属主路径下必须存活。"""
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        sid = _create_session(store, user_a, cid)
        mid = _run_async(store.save_message(sid, "assistant", "（微笑）", ""))["id"]
        _run_async(store.toggle_reaction(mid, f"char:{cid}", "😊"))

        owner = _run_async(store.get_session_reactions_owned(sid, user_a))
        users = owner.get(mid, [{}])[0].get("users", [])
        assert f"char:{cid}" in users, f"角色反应被属主过滤掉了：{owner}"


class TestDefect19Commit5OwnedPrimitives:
    """commit 5：`get_latest_review_log` 补 `_owned`（JOIN cards 收窄），与 `get_reactions` 同型。

    红源：删掉 `AND c.user_id = ?`，test_37 即红 —— 非属主会读到他人卡的审核行（发布预检的
    待审门因此可被他人卡的 flag 行误关）。
    """

    def test_37_latest_review_log_owned_sql_filter(self, store, user_a, user_b):
        tid = _create_text(store, user_a)
        cid = _create_card(store, user_a, tid)
        _run_async(store.save_review_log(f"rev_{uuid.uuid4().hex}", cid, user_a, "flag", "待审"))
        assert _run_async(store.get_latest_review_log_owned(cid, user_a))["result"] == "flag"
        assert _run_async(store.get_latest_review_log_owned(cid, user_b)) is None


# ═══════════════════════════════════════════════════════════════════════════════
# Control: User A can access own resources; nonexistent IDs return 404
# ═══════════════════════════════════════════════════════════════════════════════

class TestControlAccess:
    """Sanity checks: ownership 404 tests shouldn't break legitimate access."""

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

        monkeypatch.setattr(store, "get_card_owned", _broken)
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

        monkeypatch.setattr(store, "get_card_owned", _broken)
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
