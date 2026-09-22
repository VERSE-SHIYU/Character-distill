# -*- coding: utf-8 -*-
"""存量属主型 403 统一为 404 的验收用例（32 个端点）。

为什么是 404：403「资源存在但你没权限」与 404「不存在」的差异就是存在性枚举预言机——
攻击者拿一批 id 扫，靠状态码就能筛出哪些真实存在。非属主与不存在必须同码（本题）同文案
（TestMessageParity）。口径见 AGENTS.md §四 与 fac1a3a。

这批端点没有 storage 层的 *_owned 变体（那些只有 text / session 有），属主过滤在路由层：
「不存在 404」与「非属主 403」两个分支合并成同一个 404。所以本文件按 HTTP 面测，不按
storage 面测。

保留 403 的另两类（不得被批量改翻）：
  - B 权限型：非管理员访问管理端点、账号禁用 → 403（TestPermission403StillCoversAdminOnly）
  - C 业务门：审核待审、geo 合规 → 403（不在本文件，别处未改）
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_memory_manager, get_storage, get_tts_engine, get_voice_client
from routers.auth import get_current_user
from routers.card import router as card_router
from routers.chat import router as chat_router
from routers.distill import router as distill_router
from routers.group import router as group_router
from routers.market import router as market_router
from routers.memory import router as memory_router
from routers.message import router as message_router
from routers.voice import router as voice_router
from storage.sqlite_store import SQLiteStore


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))


@pytest.fixture
def owner():
    return f"owner_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def intruder():
    """非属主：对 owner 的资源发起请求。"""
    return f"intruder_{uuid.uuid4().hex[:8]}"


class _MemMgr:
    """启用的记忆管理器替身。禁用的真身会在属主门前返 400/空态，测不到属主门。"""

    enabled = True

    def get_all(self, card_id):
        return []

    def search(self, query, card_id, current_mood=None):
        return []

    def add(self, messages, card_id, metadata=None):
        return True

    def add_manual(self, text, card_id):
        return True

    def update(self, memory_id, text):
        return True

    def delete(self, memory_id):
        return True

    def delete_all(self, card_id):
        return True


class _VoiceClient:
    async def health_check(self):
        return False


class _StubLLM:
    """全局 LLM 的静态桩 —— 本文件的用例只用得上「它非 None」这一个事实。

    `preflight()` 是必须的那个口：`deps.get_user_llm` **每次**返回前都调它（§2.8），
    桩上缺这一格会在解析出口就 `AttributeError` 成 500，连 503 那道门都走不到。
    """

    def preflight(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_ambient_state(monkeypatch):
    """钉死两条 ambient 依赖，让用例结果只取决于被测代码，不取决于测试机。

    1. `deps.get_llm`：`deps.get_user_llm` 在用户没配 key 时 fallback 到 `get_llm()`
       （读 .env / config.yaml）。有 key 的机器上这道门开着、用例能走到属主判定；没 key
       的机器 `get_user_llm` 返 None，`create_group` 先被 503「请先在设置页配置 API Key」
       拦下 —— 断言就是巧合过的（本仓曾因此在本机绿、在无 key 机器红）。
    2. `deps.get_memory_manager`：`create_group` 是**内联** `from deps import
       get_memory_manager`，不走 `Depends`，故 `dependency_overrides` 管不到它 —— 不钉住
       就会构造真 MemoryManager（chroma → fastembed → onnxruntime），本机直接打
       `Windows fatal exception: access violation`，且结果依赖测试机 `data/` 状态。

    注意必须是 `import deps`（web/ 在 sys.path 上），**不是** `import web.deps`：本仓 web/
    无 `__init__.py`，两者虽是同一文件却是两个不同的模块对象，patch 后者打不到 router
    实际用的那份 —— 这个坑实测踩过一次，症状是「patch 了、也绿了，门其实还开着」。
    """
    import deps
    monkeypatch.setattr(deps, "get_llm", _StubLLM)
    monkeypatch.setattr(deps, "get_memory_manager", lambda: _MemMgr())


def _make_client(store, user_id):
    app = FastAPI()
    for r in (card_router, chat_router, group_router, market_router, memory_router,
              message_router, voice_router, distill_router):
        app.include_router(r)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "username": "testuser", "is_admin": False,
    }
    app.dependency_overrides[get_memory_manager] = lambda: _MemMgr()
    app.dependency_overrides[get_voice_client] = lambda: _VoiceClient()
    app.dependency_overrides[get_tts_engine] = lambda: object()
    return TestClient(app)


@pytest.fixture
def intruder_client(store, intruder):
    return _make_client(store, intruder)


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _text(store, uid):
    tid = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", "content", user_id=uid))
    return tid


def _card(store, uid):
    tid = _text(store, uid)
    cid = f"card_{uuid.uuid4().hex}"
    _run_async(store.save_card(cid, tid, "张三", '{"name": "张三"}', user_id=uid))
    return cid


def _group(store, uid):
    gid = f"grp_{uuid.uuid4().hex}"
    _run_async(store.create_group_session(gid, "群聊A", [], user_id=uid))
    return gid


def _distill_task(store, uid):
    tid = f"dt_{uuid.uuid4().hex}"
    _run_async(store.create_distill_task(
        tid, uid, "txt_x", "甲", status="running", progress_pct=0,
        message="m", card_id="", awakening="",
    ))
    return tid


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 每个改动端点：非属主 → 404，且显式 != 403
# ═══════════════════════════════════════════════════════════════════════════════

class TestCardOwnership:
    def test_get_card_avatar_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.get(f"/api/cards/{cid}/avatar")
        assert r.status_code == 404 and r.status_code != 403

    def test_save_card_avatar_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.put(f"/api/cards/{cid}/avatar", json={"data": "a" * 32})
        assert r.status_code == 404 and r.status_code != 403

    def test_export_card_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.get(f"/api/cards/{cid}/export")
        assert r.status_code == 404 and r.status_code != 403

    def test_get_card_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.get(f"/api/cards/{cid}")
        assert r.status_code == 404 and r.status_code != 403


class TestDistillOwnership:
    def test_task_status_404(self, store, owner, intruder_client):
        tid = _distill_task(store, owner)
        r = intruder_client.get(f"/api/distill/task/{tid}")
        assert r.status_code == 404 and r.status_code != 403

    def test_cancel_task_404(self, store, owner, intruder_client):
        tid = _distill_task(store, owner)
        r = intruder_client.delete(f"/api/distill/task/{tid}")
        assert r.status_code == 404 and r.status_code != 403

    def test_task_params_404(self, store, owner, intruder_client):
        tid = _distill_task(store, owner)
        r = intruder_client.get(f"/api/distill/task/{tid}/params")
        assert r.status_code == 404 and r.status_code != 403

    def test_update_card_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.patch(f"/api/distill/card/{cid}", json={"card_json": {}})
        assert r.status_code == 404 and r.status_code != 403

    def test_export_card_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.get(f"/api/distill/cards/{cid}/export")
        assert r.status_code == 404 and r.status_code != 403


class TestGroupOwnership:
    def test_create_group_with_foreign_card_404(self, store, owner, intruder_client):
        """入参 card_id 属主校验：非属主的卡与不存在的卡同判 404。

        身份用 stranger（单卡即可），否则默认 director 模式会先因「至少2个AI角色」返 400，
        到不了属主校验那一行。
        """
        cid = _card(store, owner)
        r = intruder_client.post("/api/group/create", json={
            "card_ids": [cid],
            "user_persona_type": "stranger",
            "user_persona_name": "路人",
        })
        assert r.status_code == 404 and r.status_code != 403

    def test_list_affinities_404(self, store, owner, intruder_client):
        gid = _group(store, owner)
        r = intruder_client.get(f"/api/group/{gid}/affinities")
        assert r.status_code == 404 and r.status_code != 403

    def test_toggle_reaction_404(self, store, owner, intruder_client):
        gid = _group(store, owner)
        r = intruder_client.post(f"/api/group/{gid}/message/1/react", json={"emoji": "👍"})
        assert r.status_code == 404 and r.status_code != 403

    def test_get_history_404(self, store, owner, intruder_client):
        gid = _group(store, owner)
        r = intruder_client.get(f"/api/group/{gid}/history")
        assert r.status_code == 404 and r.status_code != 403

    def test_rename_group_404(self, store, owner, intruder_client):
        gid = _group(store, owner)
        r = intruder_client.patch(f"/api/group/{gid}/rename", json={"name": "新名"})
        assert r.status_code == 404 and r.status_code != 403


class TestMarketOwnership:
    def test_publish_card_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.post(f"/api/market/{cid}/publish", json={})
        assert r.status_code == 404 and r.status_code != 403

    def test_update_published_card_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.put(f"/api/market/{cid}/publish", json={"publish_message": "x"})
        assert r.status_code == 404 and r.status_code != 403

    def test_update_card_version_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.put(f"/api/market/{cid}/versions/v1", json={"publish_message": "x"})
        assert r.status_code == 404 and r.status_code != 403

    def test_delete_market_card_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.delete(f"/api/market/{cid}")
        assert r.status_code == 404 and r.status_code != 403

    def test_batch_delete_comments_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.post(f"/api/market/{cid}/comments/batch-delete",
                                 json={"comment_ids": ["c1"]})
        assert r.status_code == 404 and r.status_code != 403

    def test_delete_foreign_comment_404(self, store, owner, intruder):
        """卡是 owner 的、评论是第三人的——非评论作者/卡作者/管理员 → 404。"""
        cid = _card(store, owner)
        comment = _run_async(store.add_comment(cid, "third_party", "第三方", "评论内容"))
        client = _make_client(store, intruder)
        r = client.delete(f"/api/market/{cid}/comments/{comment['id']}")
        assert r.status_code == 404 and r.status_code != 403

    def test_set_visibility_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.patch(f"/api/market/{cid}/visibility", json={"visibility": "private"})
        assert r.status_code == 404 and r.status_code != 403


class TestMemoryOwnership:
    def test_list_memories_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.get(f"/api/memory/list/{cid}")
        assert r.status_code == 404 and r.status_code != 403

    def test_add_memory_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.post(f"/api/memory/add/{cid}", json={"text": "记一笔"})
        assert r.status_code == 404 and r.status_code != 403

    def test_update_memory_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.put(f"/api/memory/update/m1?card_id={cid}", json={"text": "改"})
        assert r.status_code == 404 and r.status_code != 403

    def test_delete_memory_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.delete(f"/api/memory/delete/m1?card_id={cid}")
        assert r.status_code == 404 and r.status_code != 403

    def test_clear_memories_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.delete(f"/api/memory/clear/{cid}")
        assert r.status_code == 404 and r.status_code != 403


class TestMessageOwnership:
    def test_react_to_foreign_dm_404(self, store, owner, intruder_client):
        """DM 在 owner 与第三人之间，intruder 既非发件人也非收件人 → 404。"""
        msg = _run_async(store.send_message(owner, "third_party", "悄悄话"))
        r = intruder_client.post(f"/api/messages/{msg['id']}/react", json={"emoji": "👍"})
        assert r.status_code == 404 and r.status_code != 403

    def test_retract_still_403(self, store, owner, intruder_client):
        """裁决保留：仅发送者可撤回是对外可公开的规则，不是资源归属 → 仍 403。"""
        msg = _run_async(store.send_message(owner, "third_party", "悄悄话"))
        r = intruder_client.post(f"/api/messages/{msg['id']}/retract")
        assert r.status_code == 403 and r.status_code != 404


class TestVoiceOwnership:
    def test_delete_foreign_custom_voice_404(self, store, monkeypatch, intruder_client):
        import routers.voice as V
        monkeypatch.setattr(V, "_read_voice_library",
                            lambda: [{"voice_id": "v1", "ext": ".wav", "user_id": "someone_else"}])
        r = intruder_client.delete("/api/voice/v1")
        assert r.status_code == 404 and r.status_code != 403

    def test_preview_ref_audio_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.get(f"/api/voice/preview-ref/{cid}")
        assert r.status_code == 404 and r.status_code != 403

    def test_get_ref_audio_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.get(f"/api/voice/ref-audio/{cid}")
        assert r.status_code == 404 and r.status_code != 403

    def test_upload_ref_audio_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.post(
            "/api/voice/ref-audio/upload",
            data={"card_id": cid, "ref_text": ""},
            files={"file": ("a.wav", b"RIFF", "audio/wav")},
        )
        assert r.status_code == 404 and r.status_code != 403

    def test_delete_ref_audio_404(self, store, owner, intruder_client):
        cid = _card(store, owner)
        r = intruder_client.delete(f"/api/voice/ref-audio/{cid}")
        assert r.status_code == 404 and r.status_code != 403


# ═══════════════════════════════════════════════════════════════════════════════
# 2. B 权限型不得被批量改翻：非管理员访问管理端点 → 仍 403，且 != 404
# ═══════════════════════════════════════════════════════════════════════════════

class TestPermission403StillCoversAdminOnly:
    def test_market_delete_version_non_admin_403(self, store, intruder_client):
        r = intruder_client.delete("/api/market/card_x/versions/v1")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.json()}"
        assert r.status_code != 404

    def test_admin_api_non_admin_403(self, store, intruder):
        from routers.admin import router as admin_router
        app = FastAPI()
        app.include_router(admin_router)
        app.dependency_overrides[get_storage] = lambda: store
        app.dependency_overrides[get_current_user] = lambda: {
            "id": intruder, "username": "testuser", "is_admin": False,
        }
        client = TestClient(app)
        r = client.get("/api/admin/users")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.json()}"
        assert r.status_code != 404


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 文案一致性：非属主与不存在必须同一条文案（文案本身也是枚举信道）
# ═══════════════════════════════════════════════════════════════════════════════

class TestMessageParity:
    """每个资源：非属主访问 vs 访问不存在的同一个路径，detail 必须逐字相等。

    只测状态码不够——若非属主返「无权访问」、不存在返「资源不存在」，即便都是 404，
    也能靠 detail 区分。文案本身是第二条枚举信道。
    """

    def _detail(self, r):
        return r.json().get("detail")

    def test_card_parity(self, store, owner, intruder_client):
        cid = _card(store, owner)
        foreign = self._detail(intruder_client.get(f"/api/cards/{cid}"))
        missing = self._detail(intruder_client.get(f"/api/cards/nope_{uuid.uuid4().hex}"))
        assert foreign == missing

    def test_group_history_parity(self, store, owner, intruder_client):
        gid = _group(store, owner)
        foreign = self._detail(intruder_client.get(f"/api/group/{gid}/history"))
        missing = self._detail(intruder_client.get(f"/api/group/nope_{uuid.uuid4().hex}/history"))
        assert foreign == missing

    def test_distill_task_parity(self, store, owner, intruder_client):
        tid = _distill_task(store, owner)
        foreign = self._detail(intruder_client.get(f"/api/distill/task/{tid}"))
        missing = self._detail(intruder_client.get(f"/api/distill/task/nope_{uuid.uuid4().hex}"))
        assert foreign == missing

    def test_market_visibility_parity(self, store, owner, intruder_client):
        cid = _card(store, owner)
        foreign = self._detail(
            intruder_client.patch(f"/api/market/{cid}/visibility", json={"visibility": "private"}))
        missing = self._detail(
            intruder_client.patch(f"/api/market/nope_{uuid.uuid4().hex}/visibility",
                                  json={"visibility": "private"}))
        assert foreign == missing

    def test_memory_parity(self, store, owner, intruder_client):
        cid = _card(store, owner)
        foreign = self._detail(intruder_client.get(f"/api/memory/list/{cid}"))
        missing = self._detail(intruder_client.get(f"/api/memory/list/nope_{uuid.uuid4().hex}"))
        assert foreign == missing

    def test_voice_ref_audio_parity(self, store, owner, intruder_client):
        cid = _card(store, owner)
        foreign = self._detail(intruder_client.get(f"/api/voice/ref-audio/{cid}"))
        missing = self._detail(intruder_client.get(f"/api/voice/ref-audio/nope_{uuid.uuid4().hex}"))
        assert foreign == missing

    def test_dm_react_parity(self, store, owner, intruder_client):
        msg = _run_async(store.send_message(owner, "third_party", "悄悄话"))
        foreign = self._detail(
            intruder_client.post(f"/api/messages/{msg['id']}/react", json={"emoji": "👍"}))
        missing = self._detail(
            intruder_client.post(f"/api/messages/nope_{uuid.uuid4().hex}/react", json={"emoji": "👍"}))
        assert foreign == missing


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 会话属主：一对一的独立卡片会话 + 群聊内存命中（spec 72 第 1 步）
#
# 前 3 节测的是「storage 取不到行 → 404」，属主门在 SQL 里。本节的洞不一样：
# 会话**已在内存里**，取得到，于是属主门整个被跳过。所以夹具必须把会话造成
# 「内存命中」的形态（真调建会话端点 / 真建群），不能只塞 DB 行 —— 只塞 DB 行
# 时内存未命中，会走回 DB 那条已被 guard 的路，用例恒绿，测不出本步修的东西。
# ═══════════════════════════════════════════════════════════════════════════════

def _detail(r):
    return r.json().get("detail")


class _ChatLLM:
    """假 LLM：非 None 且真能出文本。

    T2/T5 是正向对照——只证「属主不被 404 挡下」不够：把整条会话建崩成 500 时
    用例照样绿。得让属主真聊到底才分得清。
    `preflight()` 与 `_StubLLM` 同因：`deps.get_user_llm` 返回前必调它（§2.8）。
    """

    last_usage: dict = {}

    def preflight(self) -> None:
        return None

    def chat(self, system_prompt, messages, *a, **kw) -> str:
        return "固定回复"

    async def achat(self, system_prompt, messages, *a, **kw) -> str:
        return "固定回复"


@pytest.fixture
def owner_client(store, owner):
    return _make_client(store, owner)


@pytest.fixture
def chat_capable_llm(monkeypatch):
    """让建会话/发消息这条链拿到 `_ChatLLM`。

    两处都要打：distill 与 chat 在**函数内** import `deps.get_user_llm`，打 deps
    那份即可；group.py 是**模块级** import，绑死在自己的命名空间里，打 deps 打不到。
    """
    import deps
    import routers.group as group_mod

    async def _fake_user_llm(*_a, **_kw):
        return _ChatLLM()

    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(group_mod, "get_user_llm", _fake_user_llm)


@pytest.fixture
def light_rag(monkeypatch):
    """把建群那条路上的 RAG 换成不碰 embedder 的空壳。

    真 RAGEngine 会 load_existing / index，落到 onnxruntime（本机 Windows 直接崩）。
    本步不测检索，让开这条下游。
    """
    import deps

    class _NoopRAG:
        def load_existing(self, *_a, **_kw):
            return None

        def index(self, *_a, **_kw):
            return None

    monkeypatch.setattr(
        deps, "get_rag_config",
        lambda: {"chunk_size": 500, "chunk_overlap": 50, "top_k": 3},
    )
    monkeypatch.setattr("core.rag.RAGEngine", lambda *_a, **_kw: _NoopRAG())


class _OrphanEngine:
    """T6 那个漏登记属主的条目里放的 engine 替身。

    只需是一个对象：属主门在 engine 被碰之前就判完了，所以它不需要任何真方法。
    """


class TestOneToOneSessionOwnership:
    """独立卡片会话（`/start_session` 不带 text_id）的内存条目必须记属主。"""

    def _start_independent_session(self, client, store, owner):
        cid = _card(store, owner)
        r = client.post("/api/distill/start_session", json={"card_id": cid})
        assert r.status_code == 200, (
            f"建会话这一步就失败了，后面的属主断言无从谈起：{r.status_code} {r.text[:200]}")
        return r.json()["session_id"]

    def test_T1_independent_session_non_owner_404(
        self, store, owner, owner_client, intruder_client, chat_capable_llm
    ):
        sid = self._start_independent_session(owner_client, store, owner)
        payload = {"session_id": sid, "message": "hi"}
        foreign = intruder_client.post("/api/chat/send", json=payload)
        assert foreign.status_code == 404, (
            f"非属主用他人的独立卡片会话发了消息：{foreign.status_code} {foreign.text[:200]}")

        missing = intruder_client.post(
            "/api/chat/send", json={"session_id": f"nope_{uuid.uuid4().hex}", "message": "hi"})
        assert foreign.status_code == missing.status_code
        assert _detail(foreign) == _detail(missing), "非属主与不存在同码但不同文案"

    def test_T2_independent_session_owner_can_chat(
        self, store, owner, owner_client, chat_capable_llm
    ):
        sid = self._start_independent_session(owner_client, store, owner)
        r = owner_client.post("/api/chat/send", json={"session_id": sid, "message": "hi"})
        assert r.status_code == 200, (
            f"属主被自己的会话当外人：{r.status_code} {r.text[:200]}")
        assert r.json().get("reply") == "固定回复", r.text[:200]

    def test_T6_unregistered_entry_fails_closed(
        self, store, owner, intruder_client, chat_capable_llm
    ):
        """条目**压根没登记**属主时也必须判「不是你的」。

        T1 测的是「登记了别人的属主」，这条测的是「没登记」—— 两种失效形态不同。
        本仓的构造点若又漏写 `user_id`，而属主门还带 `session.get("user_id") and` 前缀，
        条目就静默变成「谁都能进」。这条用例守的就是 `_ensure_session` 那句注释里的承诺：
        漏登记 → 属主本人 404（响亮），而不是放行（静默）。
        """
        import deps

        sid = f"orphan_{uuid.uuid4().hex[:12]}"
        # 最小形态：只有 engine，没有 user_id —— 就是「忘了登记」的样子。
        deps.get_sessions()[sid] = {"engine": _OrphanEngine()}
        try:
            foreign = intruder_client.post(
                "/api/chat/send", json={"session_id": sid, "message": "hi"})
            assert foreign.status_code == 404, (
                f"漏登记属主的会话条目被放行了：{foreign.status_code} {foreign.text[:200]}")

            missing = intruder_client.post(
                "/api/chat/send", json={"session_id": f"nope_{uuid.uuid4().hex}", "message": "hi"})
            assert foreign.status_code == missing.status_code
            assert _detail(foreign) == _detail(missing), "非属主与不存在同码但不同文案"
        finally:
            # 会话表是模块级全局，不清理会把条目泄给后面的用例。
            deps.get_sessions().pop(sid, None)


@pytest.fixture
def no_api_key(monkeypatch):
    """把解析出口钉成「没配 key」：`deps.get_user_llm` 返 None。

    不能只靠「本机没设 key」：autouse 的 `_no_ambient_state` 已把 `deps.get_llm` 钉成
    `_StubLLM`，而 `get_user_llm` 在用户没配 key 时正是**回落到 `get_llm()`** —— 于是默认
    返回非 None，503 门根本不开。钉解析出口，才是 S0 ⑤ 探针里那个「没配 key」的状态。
    """
    import deps

    async def _no_llm(*_a, **_kw):
        return None

    monkeypatch.setattr(deps, "get_user_llm", _no_llm)


class TestStartSessionApiKeyGate:
    """没配 key 时 `/start_session` 两条分支都必须 503，不许 200 建出 llm=None 的死会话。"""

    def test_T7_independent_card_no_key_503(self, store, owner, owner_client, no_api_key):
        cid = _card(store, owner)
        r = owner_client.post("/api/distill/start_session", json={"card_id": cid})
        assert r.status_code == 503, (
            f"没配 key 却建出了独立卡片会话：{r.status_code} {r.text[:200]}")
        assert _detail(r) == "请先在设置页配置 API Key", r.text[:200]

    def test_T8_text_branch_no_key_503(self, store, owner, owner_client, no_api_key):
        cid = _card(store, owner)
        tid = _text(store, owner)
        r = owner_client.post(
            "/api/distill/start_session", json={"card_id": cid, "text_id": tid})
        assert r.status_code == 503, (
            f"没配 key 却走进了文本分支：{r.status_code} {r.text[:200]}")
        assert _detail(r) == "请先在设置页配置 API Key", r.text[:200]


class TestGroupSessionOwnership:
    """群聊：内存命中也得判「是不是你的」，不只看删没删。"""

    def _create_owned_group(self, client, store, owner):
        cid = _card(store, owner)
        r = client.post("/api/group/create", json={
            "card_ids": [cid],
            "user_persona_type": "stranger",
            "user_persona_name": "路人",
        })
        assert r.status_code == 200, (
            f"建群这一步就失败了，后面的属主断言无从谈起：{r.status_code} {r.text[:200]}")
        return r.json()["group_id"], cid

    def test_T3_group_send_non_owner_404(
        self, store, owner, owner_client, intruder_client, chat_capable_llm, light_rag
    ):
        gid, cid = self._create_owned_group(owner_client, store, owner)
        payload = {"target_card_id": cid, "message": "hi"}
        foreign = intruder_client.post(f"/api/group/{gid}/send", json=payload)
        assert foreign.status_code == 404, (
            f"非属主往他人的群里发了消息：{foreign.status_code} {foreign.text[:200]}")

        missing = intruder_client.post(
            f"/api/group/nope_{uuid.uuid4().hex}/send", json=payload)
        assert foreign.status_code == missing.status_code
        assert _detail(foreign) == _detail(missing), "非属主与不存在同码但不同文案"

    def test_T4_group_broadcast_non_owner_404(
        self, store, owner, owner_client, intruder_client, chat_capable_llm, light_rag
    ):
        gid, cid = self._create_owned_group(owner_client, store, owner)
        payload = {"target_card_ids": [cid], "message": "hi"}
        foreign = intruder_client.post(f"/api/group/{gid}/broadcast", json=payload)
        assert foreign.status_code == 404, (
            f"非属主往他人的群广播：{foreign.status_code} {foreign.text[:200]}")

        missing = intruder_client.post(
            f"/api/group/nope_{uuid.uuid4().hex}/broadcast", json=payload)
        assert foreign.status_code == missing.status_code
        assert _detail(foreign) == _detail(missing), "非属主与不存在同码但不同文案"

    def test_T5_group_send_owner_ok(
        self, store, owner, owner_client, chat_capable_llm, light_rag
    ):
        gid, cid = self._create_owned_group(owner_client, store, owner)
        r = owner_client.post(f"/api/group/{gid}/send", json={"target_card_id": cid, "message": "hi"})
        assert r.status_code == 200, (
            f"属主被自己的群当外人：{r.status_code} {r.text[:200]}")
        assert r.json().get("reply") == "固定回复", r.text[:200]
