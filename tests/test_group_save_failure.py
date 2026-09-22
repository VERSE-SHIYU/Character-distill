# -*- coding: utf-8 -*-
"""群聊广播：三类保存各自为政，失败的那一条照发、并由帧上的 `saved` 自己声明。

口径（用户已确认）：保存失败**不中断**这一轮，用户可见行为与一对一一致 —— 失败的那条
消息在帧上带 `saved: false`，前端据此标「未保存，刷新后会丢失」。

改前形态：`broadcast` 的三类保存（user / silent / assistant）裸露在外层 `try` 里，
任一失败即整轮中断；且 `reply` 帧只在保存成功后才发 —— 角色已经说完的话因为写库失败
从用户眼前消失。本文件锁的就是这两条。

判据落在**帧**上而不是库上：库那一面由 `tests/test_group_save_failure` 之外的 store 测试
守着，这里要证的是「用户还看得到那句话」。
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_storage
from routers.auth import get_current_user
from routers.group import router as group_router
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
    return SQLiteStore(str(tmp_path / f"{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_id():
    return f"u_{uuid.uuid4().hex[:8]}"


class _ReplyLLM:
    """假 LLM：`achat` 返回固定文本。

    `reply` 是**类属性**，用例改它就能让某个角色说 `[SILENT]`（G3 要走到 silent 那条
    分支，随机回复走不到）。与 test_ownership_404 的 `_ChatLLM` 同形：`preflight` 是
    `deps.get_user_llm` 返回前必调的那一格，缺了会在解析出口就 AttributeError。
    """

    reply = "固定回复"
    # 非空时按序取值（G5 要「先来一条 [REACT:…]，再来一条正常回复」）。类属性，实例共享。
    replies: list[str] = []
    last_usage: dict = {}

    def preflight(self) -> None:
        return None

    def chat(self, *a, **kw) -> str:
        return type(self).replies.pop(0) if type(self).replies else type(self).reply

    async def achat(self, *a, **kw) -> str:
        return type(self).replies.pop(0) if type(self).replies else type(self).reply


class _MemMgr:
    """启用的记忆管理器替身 —— 真身会拉 chroma → fastembed → onnxruntime（本机崩）。"""

    enabled = True

    def get_all(self, card_id):
        return []

    def search(self, query, card_id, current_mood=None):
        return []

    def add(self, messages, card_id, metadata=None):
        return True


class _NoopRAG:
    def load_existing(self, *_a, **_kw):
        return None

    def index(self, *_a, **_kw):
        return None

    def query_with_emotion_ex(self, *_a, **_kw):
        return []


@pytest.fixture(autouse=True)
def _no_ambient_state(monkeypatch):
    """钉死 ambient 依赖，让结果只取决于被测代码。

    两处都要打 `get_user_llm`：group.py 是**模块级** import，绑死在自己的命名空间里，
    打 `deps` 那份打不到它。
    """
    import deps
    import routers.group as group_mod

    async def _fake_user_llm(*_a, **_kw):
        return _ReplyLLM()

    monkeypatch.setattr(deps, "get_llm", _ReplyLLM)
    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(group_mod, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_memory_manager", lambda: _MemMgr())
    monkeypatch.setattr(
        deps, "get_rag_config",
        lambda: {"chunk_size": 500, "chunk_overlap": 50, "top_k": 3},
    )
    monkeypatch.setattr("core.rag.RAGEngine", lambda *_a, **_kw: _NoopRAG())


class _FailingGroupSave:
    """包住真 store：只让指定 role 的 `save_group_message` 失败，其余全部转发。

    失败面收窄到「这一条消息」才分得开「三类各自 nonfatal」与「一个块串起两笔」——
    整体失败的话，改前改后都是同一个 500，看不出差别。
    """

    def __init__(self, inner: SQLiteStore, fail_roles: set[str], fail_reaction: bool = False) -> None:
        self._inner = inner
        self._fail_roles = fail_roles
        self._fail_reaction = fail_reaction
        self.saved: list[str] = []
        self.reaction_attempts = 0

    async def save_group_message(
        self, group_id, speaker, role, content, speaker_card_id="", **kw
    ):
        if role in self._fail_roles:
            raise RuntimeError(f"{role} save down")
        self.saved.append(role)
        return await self._inner.save_group_message(
            group_id, speaker, role, content, speaker_card_id, **kw
        )

    async def toggle_reaction(self, *a, **kw):
        # 先记「确实被调到」再决定失败 —— 只看失败与否，分不出「没走到这条路」与「走到了」。
        self.reaction_attempts += 1
        if self._fail_reaction:
            raise RuntimeError("reaction down")
        return await self._inner.toggle_reaction(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _client(storage, user_id):
    app = FastAPI()
    app.include_router(group_router)
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "username": "testuser", "role": "user",
    }
    return TestClient(app)


def _make_owned_group(client, store, uid) -> tuple[str, str]:
    """建一张卡再建群 —— 群聊要靠卡才能有引擎，引擎才有 LLM 可叫。"""
    tid = f"txt_{uuid.uuid4().hex}"
    cid = f"card_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", "content", user_id=uid))
    _run_async(store.save_card(cid, tid, "张三", '{"name": "张三"}', user_id=uid))
    r = client.post("/api/group/create", json={
        "card_ids": [cid],
        "user_persona_type": "stranger",
        "user_persona_name": "路人",
    })
    assert r.status_code == 200, (
        f"建群这一步就失败了，后面的帧断言无从谈起：{r.status_code} {r.text[:200]}")
    return r.json()["group_id"], cid


def _make_two_card_group(client, store, uid) -> tuple[str, list[str]]:
    """两个角色的群 —— 反应是**一条回复**（走完就 continue，不发帧），要另有一条正常回复
    才能同时验「反应失败」与「本轮照常收尾」。"""
    tid = f"txt_{uuid.uuid4().hex}"
    c1, c2 = f"card_{uuid.uuid4().hex}", f"card_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", "content", user_id=uid))
    _run_async(store.save_card(c1, tid, "张三", '{"name": "张三"}', user_id=uid))
    _run_async(store.save_card(c2, tid, "李四", '{"name": "李四"}', user_id=uid))
    r = client.post("/api/group/create", json={
        "card_ids": [c1, c2],
        "user_persona_type": "stranger",
        "user_persona_name": "路人",
    })
    assert r.status_code == 200, (
        f"建群这一步就失败了，后面的帧断言无从谈起：{r.status_code} {r.text[:200]}")
    return r.json()["group_id"], [c1, c2]


def _broadcast(client, gid, cid) -> list[dict]:
    ids = cid if isinstance(cid, list) else [cid]
    r = client.post(f"/api/group/{gid}/broadcast",
                    json={"target_card_ids": ids, "message": "hi"})
    assert r.status_code == 200, f"广播这一层就失败了：{r.status_code} {r.text[:200]}"
    frames = []
    for line in r.text.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[6:]))
    return frames


def _one(frames: list[dict], key: str) -> dict:
    hits = [f for f in frames if key in f]
    assert len(hits) == 1, f"应恰有一帧带 {key}，实得 {len(hits)}：{frames}"
    return hits[0]


def _assert_no_error_frame(frames: list[dict]) -> None:
    errors = [f for f in frames if "error" in f]
    assert errors == [], f"某一笔保存失败把整轮广播打断了：{errors}"


# ═══════════════════════════════════════════════════════════════════════════════
# G1–G3：三类保存各自失败，帧照发、`saved` 自己声明
# ═══════════════════════════════════════════════════════════════════════════════

def test_G1_assistant_save_failure_still_delivers_the_reply(store, user_id):
    """角色回复存失败 → 仍发 `reply` 帧（`saved=false`、`msg_id=null`），本轮照常收尾。"""
    failing = _FailingGroupSave(store, {"assistant"})
    client = _client(failing, user_id)
    gid, cid = _make_owned_group(client, store, user_id)

    frames = _broadcast(client, gid, cid)

    _assert_no_error_frame(frames)
    reply = _one(frames, "reply")
    assert reply["reply"] == "固定回复", "角色已经说完的话因为写库失败从用户眼前消失了"
    assert reply["saved"] is False, "没存上却没告诉前端 —— 用户无从知道这条刷新后会丢"
    assert reply["msg_id"] is None, "没入库却给了一个消息 id"
    assert _one(frames, "done")
    assert failing.saved == ["user"], f"实际写进库的 role 不对：{failing.saved}"


def test_G2_user_save_failure_still_runs_the_round(store, user_id):
    """用户消息存失败 → `user` 帧仍发（`saved=false`），角色回复照常生成并发出。"""
    failing = _FailingGroupSave(store, {"user"})
    client = _client(failing, user_id)
    gid, cid = _make_owned_group(client, store, user_id)

    frames = _broadcast(client, gid, cid)

    _assert_no_error_frame(frames)
    user_frames = [f for f in frames if f.get("type") == "user"]
    assert len(user_frames) == 1, f"应有恰一个 user 帧，实得 {len(user_frames)}：{frames}"
    assert user_frames[0]["saved"] is False, "用户那条没存上，帧却报成功"
    assert user_frames[0]["msg_id"] is None

    replies = [f for f in frames if f.get("type") == "reply"]
    assert len(replies) == 1, f"用户那条存失败把角色回复也带走了：{frames}"
    assert replies[0]["saved"] is True and replies[0]["msg_id"] is not None
    assert failing.saved == ["assistant"], f"实际写进库的 role 不对：{failing.saved}"
    assert _one(frames, "done")


def test_G3_silent_save_failure_still_delivers_the_silent_mark(store, user_id):
    """`silent`（[SILENT] 那条）存失败 → 同 G1：帧照发、`saved=false`、`msg_id=null`。"""
    _ReplyLLM.reply = "[SILENT]"
    try:
        failing = _FailingGroupSave(store, {"silent"})
        client = _client(failing, user_id)
        gid, cid = _make_owned_group(client, store, user_id)

        frames = _broadcast(client, gid, cid)
    finally:
        _ReplyLLM.reply = "固定回复"

    _assert_no_error_frame(frames)
    reply = _one(frames, "reply")
    assert reply["role"] == "silent", f"没走到 silent 那条分支，本用例没验到东西：{reply}"
    assert reply["saved"] is False, "沉默那条没存上，帧却报成功"
    assert reply["msg_id"] is None
    assert _one(frames, "done")
    assert failing.saved == ["user"], f"实际写进库的 role 不对：{failing.saved}"


# ═══════════════════════════════════════════════════════════════════════════════
# G5：角色反应写入失败 —— 反应不是消息，没有「未保存」可标，但也不能把整轮带走
# ═══════════════════════════════════════════════════════════════════════════════

def test_G5_reaction_save_failure_still_finishes_the_round(store, user_id):
    """角色点了赞但写库失败 → 本轮照常把回复发完并 `done`，无 error 帧。"""
    _ReplyLLM.replies = ["[REACT:👍]", "固定回复"]
    try:
        failing = _FailingGroupSave(store, set(), fail_reaction=True)
        client = _client(failing, user_id)
        gid, cids = _make_two_card_group(client, store, user_id)

        frames = _broadcast(client, gid, cids)
    finally:
        _ReplyLLM.replies = []

    assert failing.reaction_attempts == 1, (
        f"没走到写反应那条路，本用例没验到东西：attempts={failing.reaction_attempts}，frames={frames}")
    _assert_no_error_frame(frames)
    assert _one(frames, "done"), f"反应写失败把本轮收尾带走了：{frames}"
    replies = [f for f in frames if f.get("type") == "reply"]
    assert len(replies) == 1 and replies[0]["reply"] == "固定回复", (
        f"反应写失败影响了同一轮里其他角色的回复：{frames}")
