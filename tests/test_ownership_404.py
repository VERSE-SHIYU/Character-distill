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


def _make_client(store, user_id):
    app = FastAPI()
    for r in (card_router, group_router, market_router, memory_router,
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
