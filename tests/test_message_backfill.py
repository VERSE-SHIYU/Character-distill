# -*- coding: utf-8 -*-
"""消息补写：写失败的消息**不丢**，留在会话队列里按原顺序补上。

队列本身（顺序不变量、可达性分辨、丢弃粒度）在 `tests/test_message_outbox.py` 里测，
那里不碰存储。本文件测的是**接线**：一对一这条链上每个写入点确实走了队列，四个补写
时机（下一笔写、重试接口、空闲清理、关停）确实补。

**存储替身有两个旋钮**（对账表 C1 的触发形态就是这个形状：「第一轮让 char 写失败
（ping 失败）」）：

  * `fail_roles` —— 哪几条消息写不进去；
  * `ping_ok`   —— 队列把它当「库挂了」还是「这条坏了」。

两者分开是必须的：`ping` 不通 → 整队保留、这条是 `pending`；`ping` 通 → 重试一次
仍不行就 `failed` 并出队。同一个写失败，前端看到的是两个不同的提示、两个不同的动作
（有重试按钮 / 没有），分不开就测不出接线对不对。
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_memory_manager, get_storage
from limiter import limiter
from routers.auth import get_current_user
from routers.chat import router as chat_router
from routers.distill import router as distill_router
from routers.group import router as group_router
from routers.history import router as history_router
from storage.sqlite_store import SQLiteStore
from conftest import open_test_client, registered_globals, app_lifespan


# ── 夹具 ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"{uuid.uuid4().hex}.db"))


@pytest.fixture
def owner():
    return f"owner_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def intruder():
    return f"intruder_{uuid.uuid4().hex[:8]}"


class _MemMgr:
    """启用的记忆管理器替身 —— 真身会拉 chroma → fastembed → onnxruntime（本机崩）。"""

    enabled = True

    def get_all(self, card_id):
        return []

    def search(self, query, card_id, current_mood=None):
        return []

    def add(self, messages, card_id, metadata=None):
        return True


class _ChatLLM:
    """假 LLM：非 None 且真能出文本（开场白与聊天都靠它）。"""

    last_usage: dict = {}

    def preflight(self) -> None:
        return None

    def chat(self, *a, **kw) -> str:
        return "固定回复"

    async def achat(self, *a, **kw) -> str:
        return "固定回复"


class _FlakyStore:
    """可编程存储替身：按 role 让某次写入抛，或让 ping 报不可达。

    只包住 `save_message` 与 `ping`，其余（建卡、读消息、建会话…）全部转发给真库 ——
    失败面收窄到「消息落库」这一处，才分得清「队列把它接住了」与「整条路都断了」。
    """

    def __init__(self, inner: SQLiteStore) -> None:
        self._inner = inner
        self.fail_roles: set[str] = set()
        self.ping_ok = True
        self.wrote: list[tuple[str, int]] = []
        self.ping_calls = 0

    async def save_message(self, session_id, role, content, rag_context, **kw):
        if role in self.fail_roles:
            raise RuntimeError(f"{role} save down")
        rec = await self._inner.save_message(session_id, role, content, rag_context, **kw)
        self.wrote.append((role, rec["id"]))
        return rec

    async def save_group_message(self, group_id, speaker, role, content, *, speaker_card_id="", **kw):
        if role in self.fail_roles:
            raise RuntimeError(f"{role} save down")
        # 与一对一的 `save_message` 不同：这一格返回的是行号（int），不是记录。
        row_id = await self._inner.save_group_message(
            group_id, speaker, role, content, speaker_card_id=speaker_card_id, **kw,
        )
        self.wrote.append((role, row_id))
        return row_id

    async def ping(self):
        self.ping_calls += 1
        if not self.ping_ok:
            raise RuntimeError("db down")
        return await self._inner.ping()

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def roles(self) -> list[str]:
        return [role for role, _ in self.wrote]


@pytest.fixture
def flaky(store, monkeypatch):
    """把存储换成可编程替身，并同步钉住 `deps._storage`（缺陷 117：库只有一个来源）。"""
    import deps

    st = _FlakyStore(store)
    monkeypatch.setattr(deps, "_storage", st)
    return st


@pytest.fixture(autouse=True)
def _no_ambient_state(monkeypatch):
    """钉死 ambient 依赖，让结果只取决于被测代码，不取决于测试机有没有配 key。"""
    import deps
    import routers.group as group_mod

    async def _fake_user_llm(*_a, **_kw):
        return _ChatLLM()

    monkeypatch.setattr(deps, "get_llm", _ChatLLM)
    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(group_mod, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_memory_manager", lambda: _MemMgr())
    monkeypatch.setattr(deps, "get_rag_config",
                        lambda: {"chunk_size": 500, "chunk_overlap": 50, "top_k": 3})
    monkeypatch.setattr("core.rag.RAGEngine", lambda *_a, **_kw: _NoopRAG())


class _NoopRAG:
    """真 RAGEngine 会 load_existing / index，落到 onnxruntime（本机 Windows 直接崩）。"""

    def load_existing(self, *_a, **_kw):
        return None

    def index(self, *_a, **_kw):
        return None

    def query_with_emotion_ex(self, *_a, **_kw):
        return []


@pytest.fixture(autouse=True)
def _isolate_deps_state(monkeypatch):
    """`_sessions` / `_group_sessions` / `_main_loop` 是进程级全局，用例之间不许互漏。"""
    import deps

    monkeypatch.setattr(deps, "_main_loop", deps._main_loop)
    sessions = deps.get_sessions()
    groups = deps.get_group_sessions()
    yield
    sessions.clear()
    groups.clear()


@pytest.fixture(autouse=True)
def _rate_limit_off(monkeypatch):
    """关掉限流器 —— 一个用例会打多次同一个端点（`/flush` 带 `@limiter.limit`）。"""
    import limiter as _lim_

    monkeypatch.setattr(_lim_.limiter, "enabled", False)


def _client(st: _FlakyStore, user_id: str) -> TestClient:
    app = FastAPI(lifespan=app_lifespan)
    for r in (chat_router, distill_router, history_router, group_router):
        app.include_router(r)
    app.state.limiter = limiter
    app.dependency_overrides[get_storage] = lambda: st
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "username": "testuser", "role": "user",
    }
    app.dependency_overrides[get_memory_manager] = lambda: _MemMgr()
    return open_test_client(app)


def _run(coro):
    return asyncio.run(coro)


def _registrations() -> tuple:
    """进程级注册的三件套（主 loop / 投递实现 / 调用守卫）—— 缺陷 113 的观测量。"""
    import adapters.llm_adapter as llm_adapter
    import core.scheduling as scheduling
    import deps

    return (deps.get_main_loop(), scheduling.get_loop_submitter(), llm_adapter.get_call_guard())


# ── 被测对象的替身 ────────────────────────────────────────────────────────────

class _Engine:
    """够 `_do_chat` / `_do_chat_stream` 两条路跑完的最小引擎替身。

    `summary` 非空才会走到摘要那一段，故它是参数而不是常量。
    """

    def __init__(self, pieces=("回", "复"), summary: str = ""):
        self.history: list[dict] = []
        self.last_traces: list = []
        self.last_summary = summary
        self._last_rag_context = ""
        self._ctx_engine = type("Ctx", (), {"web_search_enabled": False})()
        self.affinity_enabled = True
        self.agent_mode = False
        self._pieces = list(pieces)

    def chat(self, *a, **kw) -> str:
        return "回复"

    def chat_stream(self, *a, **kw):
        return iter(self._pieces)

    def post_stream_process(self, *a, **kw):
        return None

    def _should_retract(self, reply) -> bool:
        return False


def _install_session(st, sid: str, user_id: str, engine) -> dict:
    """把一个内存命中的会话条目塞进 deps 的会话表（属主已登记），并在库里补齐它的卡与会话行。

    不走 `/start_session` 是为了让「本轮写了哪几笔」完全由用例说了算 —— 建会话本身
    会写一条开场白，那条会把 C1 的行号算术搅乱。但库里的 `sessions` 行不能省：
    `messages.session_id` 有外键，没有它每一笔写入都是 `FOREIGN KEY constraint failed`，
    测出来的会是「整条路都断了」，而不是「队列把它接住了」。
    """
    import deps

    from core.text_manager import new_session_entry

    _run(st.save_session(sid, _card(st, user_id), "stranger", "", user_id))
    # 条目形状只从 `new_session_entry` 拿（不手搓 dict），也不在这里补字段 ——
    # 在调用点补等于把「条目长什么样」又拆成两处定义。
    sess = new_session_entry(engine, None, user_id)
    deps.get_sessions()[sid] = sess
    return sess


def _new_sid() -> str:
    return f"s_{uuid.uuid4().hex}"


def _backdate(store, sid: str, hours: int = 12) -> None:
    """把会话的 `updated_at` 往前挪 —— 重逢问候要求距上次活动 6 小时以上。"""

    async def _go():
        # 正常退出即提交（_ConnectionContext 的契约），不必自己 commit
        async with await store._connect() as conn:
            await conn.execute(
                "UPDATE sessions SET updated_at = datetime('now', ?) WHERE id = ?",
                (f"-{hours} hours", sid),
            )

    _run(_go())


def _card(st, uid) -> str:
    tid = f"txt_{uuid.uuid4().hex}"
    cid = f"card_{uuid.uuid4().hex}"
    _run(st.save_text(tid, "src.txt", "content", user_id=uid))
    _run(st.save_card(cid, tid, "张三", '{"name": "张三"}', user_id=uid))
    return cid


def _stream(client: TestClient, sid: str, message: str = "hi") -> list[dict]:
    """打 `/api/chat/send` 的流式分支，按到达顺序返回帧。"""
    r = client.post("/api/chat/send",
                    json={"session_id": sid, "message": message, "stream": True})
    assert r.status_code == 200, f"发消息这一步就失败了：{r.status_code} {r.text[:300]}"
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def _done(frames: list[dict]) -> dict:
    hits = [f for f in frames if f.get("done") is True]
    assert len(hits) == 1, f"应恰有一个 done 帧，实得 {len(hits)}：{frames}"
    return hits[0]


def _flush(client: TestClient, sid: str) -> dict:
    r = client.post(f"/api/chat/{sid}/flush", json={})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    return r.json()


def _make_group(st, uid, n: int = 1) -> tuple[str, list[str]]:
    """建 n 张同文本的卡再建群 —— 群聊要有引擎才发得出话，引擎要有卡。"""
    tid = f"txt_{uuid.uuid4().hex}"
    cards = []
    _run(st.save_text(tid, "src.txt", "content", user_id=uid))
    for i in range(n):
        cid = f"card_{uuid.uuid4().hex}"
        _run(st.save_card(cid, tid, f"角色{i}", f'{{"name": "角色{i}"}}', user_id=uid))
        cards.append(cid)
    client = _client(st, uid)
    r = client.post("/api/group/create", json={
        "card_ids": cards,
        "user_persona_type": "stranger",
        "user_persona_name": "路人",
    })
    assert r.status_code == 200, f"建群这一步就失败了：{r.status_code} {r.text[:300]}"
    return r.json()["group_id"], cards


def _group_send(client: TestClient, gid: str, cid: str, message: str) -> dict:
    r = client.post(f"/api/group/{gid}/send", json={"target_card_id": cid, "message": message})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    return r.json()


def _group_frames(client: TestClient, gid: str, message: str, cards: list[str]) -> list[dict]:
    """打 `/api/group/{gid}/broadcast` 的流式分支，按到达顺序返回帧。"""
    r = client.post(f"/api/group/{gid}/broadcast",
                    json={"message": message, "target_card_ids": cards})
    assert r.status_code == 200, f"广播这一步就失败了：{r.status_code} {r.text[:300]}"
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def _run_cleanup_once(monkeypatch) -> None:
    """把空闲清理循环跑完**一轮**就停（它是 `while True` + `sleep(300)`）。"""
    import deps

    class _Stop(Exception):
        pass

    calls = {"n": 0}

    async def _sleep(_seconds):
        calls["n"] += 1
        if calls["n"] > 1:
            raise _Stop()

    monkeypatch.setattr(deps.asyncio, "sleep", _sleep)
    monkeypatch.setattr(deps, "_SESSION_IDLE_TTL", -1)
    with pytest.raises(_Stop):
        _run(deps._session_cleanup_loop())


# ═══════════════════════════════════════════════════════════════════════════════
# C1–C4：一对一这条链上的接线
# ═══════════════════════════════════════════════════════════════════════════════

def test_C1_failed_char_save_is_backfilled_next_round_in_order(flaky, owner):
    """角色回复存失败 → done 帧报 `pending`；下一轮把它按原顺序补上（id 排在队尾之后）。

    判据是**两件事一起**：那一笔确实补上了（key 出现在 `flushed` 里），且补上的 id
    **小于**本轮用户消息的 id —— 只断言「补上了」的话，把补写放到本轮写入之后
    （顺序反了）也绿，而用户看到的历史就是错序的。
    """
    sid = _new_sid()
    _install_session(flaky, sid, owner, _Engine())
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done1 = _done(_stream(client, sid, "第一句"))
    key = done1["char_save"]["key"]
    assert done1["char_save"]["state"] == "pending", "库不可达时那条该留在队里，不是判死"
    assert done1["char_msg_id"] is None
    assert flaky.roles() == ["user"], f"用户那条应已落库、角色那条应还在队里：{flaky.wrote}"

    flaky.fail_roles = set()
    flaky.ping_ok = True
    done2 = _done(_stream(client, sid, "第二句"))

    flushed = {f["key"]: f["id"] for f in done2["flushed"]}
    assert key in flushed, f"上一轮没入库的那条没被补写：{done2['flushed']}"
    assert flushed[key] < done2["user_msg_id"], (
        "补写的那条排到了本轮消息后面 —— 历史顺序反了")
    assert flaky.roles() == ["user", "char", "user", "char"], flaky.wrote


def test_C2_summary_queues_behind_an_unpersisted_message(flaky, owner):
    """摘要走队列：前一条还在队里时，摘要不许先落库。

    改前形态（变异）是「摘要绕过队列直接写」—— 它会在前一条还没落库时抢到更小的行 id，
    用户刷新后看到的摘要就插到了那条消息前面。
    """
    sid = _new_sid()
    _install_session(flaky, sid, owner, _Engine(summary="新摘要"))
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done = _done(_stream(client, sid, "第一句"))
    assert done["char_save"]["state"] == "pending"

    assert "summary" not in flaky.roles(), (
        f"前一条还在队里，摘要却先落了库：{flaky.wrote}")

    flaky.fail_roles = set()
    flaky.ping_ok = True
    _flush(client, sid)
    assert flaky.roles() == ["user", "char", "summary"], flaky.wrote


def test_C3_revoke_clears_the_queue(flaky, owner):
    """`/revoke` 之后队里不留这条 —— 删库成功即清队，补写不会把刚撤回的消息又写回来。"""
    sid = _new_sid()
    _install_session(flaky, sid, owner, _Engine())
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done = _done(_stream(client, sid, "第一句"))
    assert done["char_save"]["state"] == "pending"

    r = client.post("/api/chat/revoke", json={"session_id": sid, "message_id": 1})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"

    import deps
    assert not deps.get_sessions()[sid]["outbox"].has_pending, "撤回之后队里还留着这条"

    flaky.fail_roles = set()
    flaky.ping_ok = True
    assert _flush(client, sid) == {"flushed": [], "dropped": []}
    assert "char" not in flaky.roles(), f"撤回掉的消息被补写又写回来了：{flaky.wrote}"


def test_C4_rollback_discards_a_user_message_that_never_landed(flaky, owner):
    """一个 token 都没产出的回滚分支：那条没入库的用户消息要从队里摘掉。

    只删库不摘队的话，补写会把这条「用户看到失败了」的消息又写回来 —— 界面上是
    「发送失败」的那句话，刷新之后自己冒出来了。
    """
    sid = _new_sid()
    engine = _Engine()

    def _boom(*a, **kw):
        raise RuntimeError("stream down")

    engine.chat_stream = _boom
    _install_session(flaky, sid, owner, engine)
    client = _client(flaky, owner)

    flaky.fail_roles = {"user"}
    flaky.ping_ok = False
    frames = _stream(client, sid, "第一句")

    assert [f for f in frames if "error" in f], f"流式失败却没发 error 帧：{frames}"

    import deps
    assert not deps.get_sessions()[sid]["outbox"].has_pending, "回滚之后队里还留着那条用户消息"

    flaky.fail_roles = set()
    flaky.ping_ok = True
    assert _flush(client, sid) == {"flushed": [], "dropped": []}
    assert flaky.roles() == [], f"回滚掉的消息被补写又写回来了：{flaky.wrote}"


# ═══════════════════════════════════════════════════════════════════════════════
# C5–C6：两处角色消息保存点（开场白、重逢问候）
#
# 这两处原先「进记忆」与「写库」绑在同一个块里 —— 写库失败时记忆里也没有这条，用户
# 看到的第一句话在刷新后就消失了。判据是**两件事分开**：记忆里有（本轮体验完整），
# 返回体带 `save`（前端知道它会丢）。
# ═══════════════════════════════════════════════════════════════════════════════

def test_C5_opening_reaches_history_and_the_body_when_its_save_fails(flaky, owner):
    """开场白写失败 → 仍进 `engine.history`，返回体带 `first_message_save`。"""
    import deps

    cid = _card(flaky, owner)
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    r = client.post("/api/distill/start_session", json={"card_id": cid})
    assert r.status_code == 200, f"建会话这一步就失败了：{r.status_code} {r.text[:300]}"
    body = r.json()
    sid = body["session_id"]

    assert body["first_message"] == "固定回复"
    assert body["first_message_save"]["state"] == "pending", body.get("first_message_save")
    assert body["first_created_at"] == "", "没有这条记录却给了时间戳 —— 假默认值"

    history = deps.get_sessions()[sid]["engine"].history
    assert history and history[-1] == {"role": "assistant", "content": "固定回复"}, (
        f"开场白没进记忆 —— 用户看到的第一句话刷新后就没了：{history}")


def test_C6_reunion_greeting_reaches_messages_when_its_save_fails(flaky, store, owner):
    """重逢问候写失败 → 仍出现在 `messages` 末尾，且带 `save`（前端据此标「未保存」）。"""
    import deps

    cid = _card(flaky, owner)
    client = _client(flaky, owner)
    r = client.post("/api/distill/start_session", json={"card_id": cid})
    assert r.status_code == 200, f"建会话这一步就失败了：{r.status_code} {r.text[:300]}"
    sid = r.json()["session_id"]

    # 模拟重启：内存里没有这条会话，resume 从库里重建引擎（记忆因此非空，问候才会生成）
    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    deps.get_sessions().pop(sid, None)
    _backdate(store, sid)

    r = client.post(f"/api/history/{sid}/resume", json={})
    assert r.status_code == 200, f"重逢这一步就失败了：{r.status_code} {r.text[:300]}"
    tail = r.json()["messages"][-1]

    assert tail["reunion"] is True, f"末尾这条不是重逢问候：{tail}"
    assert tail["content"] == "固定回复"
    assert tail["save"]["state"] == "pending", tail.get("save")
    assert tail["id"] is None, "没入库却给了一个消息 id"

    engine = deps.get_sessions()[sid]["engine"]
    # 数**条数**而不是看末条：开场白与问候在这套假 LLM 下文本相同（都是「固定回复」），
    # 只看 `history[-1]` 的话，问候没进记忆也照样绿 —— 末位还站着库里重建出来的开场白。
    assistants = [m for m in engine.history if m["role"] == "assistant"]
    assert len(assistants) == 2, f"问候没进记忆（库里重建出 1 条开场白，应再加 1 条）：{engine.history}"


# ═══════════════════════════════════════════════════════════════════════════════
# C7–C9：另外三个补写时机
# ═══════════════════════════════════════════════════════════════════════════════

def test_C7_flush_endpoint_backfills_and_hides_foreign_sessions(flaky, owner, intruder):
    """重试接口：属主调它真补上；非属主与「不存在」同码同文案。"""
    sid = _new_sid()
    _install_session(flaky, sid, owner, _Engine())
    owner_client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done = _done(_stream(owner_client, sid, "第一句"))
    key = done["char_save"]["key"]

    flaky.fail_roles = set()
    flaky.ping_ok = True
    body = _flush(owner_client, sid)
    assert [f["key"] for f in body["flushed"]] == [key], body
    assert body["flushed"][0]["id"] > done["user_msg_id"], "补上的那条排到了它前面那条之前"

    intruder_client = _client(flaky, intruder)
    foreign = intruder_client.post(f"/api/chat/{sid}/flush", json={})
    missing = intruder_client.post(f"/api/chat/nope_{uuid.uuid4().hex}/flush", json={})
    assert foreign.status_code == 404, (
        f"非属主用他人的会话调重试接口：{foreign.status_code} {foreign.text[:200]}")
    assert foreign.status_code == missing.status_code
    assert foreign.json()["detail"] == missing.json()["detail"], "非属主与不存在同码不同文案"


def test_C8_idle_cleanup_backfills_before_evicting(flaky, owner, monkeypatch):
    """会话过期出队**之前**补写 —— 出队之后队列跟着没了，没补上的消息永久丢。"""
    import deps

    sid = _new_sid()
    _install_session(flaky, sid, owner, _Engine())
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done = _done(_stream(client, sid, "第一句"))
    assert done["char_save"]["state"] == "pending"

    flaky.fail_roles = set()
    flaky.ping_ok = True
    sessions = deps.get_sessions()
    monkeypatch.setattr(deps, "get_sessions", lambda: sessions)

    _run_cleanup_once(monkeypatch)

    assert sid not in sessions, "过期会话没被清出去，本用例没验到出队那一步"
    assert "char" in flaky.roles(), f"出队前没补写，那条消息跟着队列一起没了：{flaky.wrote}"


def test_C8b_idle_cleanup_keeps_a_session_whose_messages_have_not_landed(
    flaky, owner, monkeypatch,
):
    """库不可达时补写补不上 → 会话**不能**出队；库回来后的那一轮才出队。

    C8 走的是「清理跑到时库已经好了」那半边：补写成功、会话出队，两个断言都过。真正会丢
    消息的是另一半 —— 清理跑到时库还不可达，`flush` 有意整队保留（不是这条写不进去，是
    此刻问不着库），这时照样 `pop` 就是**把队列连同里面的消息一起扔掉**，用户那边只在
    上一轮看到过一个「未保存」。
    """
    import deps

    sid = _new_sid()
    _install_session(flaky, sid, owner, _Engine())
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done = _done(_stream(client, sid, "第一句"))
    assert done["char_save"]["state"] == "pending"

    sessions = deps.get_sessions()
    monkeypatch.setattr(deps, "get_sessions", lambda: sessions)

    # 第一轮：库还没回来 → 补写补不上 → 会话留着、队列留着
    _run_cleanup_once(monkeypatch)
    assert sid in sessions, "库不可达时把会话清出去了 —— 队里那条消息跟着永久丢了"
    assert sessions[sid]["outbox"].has_pending, "队列被清空了，没补上的消息丢了"

    # 第二轮：库回来了 → 补上、出队
    flaky.fail_roles = set()
    flaky.ping_ok = True
    _run_cleanup_once(monkeypatch)
    assert sid not in sessions, "库好了这一轮该出队了，本用例没验到出队那一步"
    assert "char" in flaky.roles(), f"出队前没补写：{flaky.wrote}"


def test_C9_shutdown_backfills_the_queues(flaky, owner, monkeypatch):
    """关停时补写（在取消清理循环之后）—— 队列在内存里，进程一走就没了。

    同时是缺陷 113 的复现：`_lifespan` 装上进程级注册却不还原，注册就留在了后面。
    注册是同一个函数对象（`deps._submit_to_main_loop`），所以「还回没还回」分辨不出，
    真正的差别是它指向哪个 loop —— 留在后面的是 `_run(_drive())` 里那个**已经关掉**
    的 loop：之后任何走 `submit_to_main_loop` 的用例都把协程投到死 loop 上，
    `chat_engine` 的宽 `except` 把异常吞掉，只在**别的**用例头上飘一句
    `coroutine ... was never awaited`。
    """
    import server as server_mod

    sid = _new_sid()
    _install_session(flaky, sid, owner, _Engine())
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done = _done(_stream(client, sid, "第一句"))
    assert done["char_save"]["state"] == "pending"

    flaky.fail_roles = set()
    flaky.ping_ok = True

    # 关掉启动期的凭据校验与守卫装配：本用例测的是**退出**那一段，且要能在没有
    # `.env` / `config.yaml` 的机器上跑绿。
    monkeypatch.setattr(server_mod, "validate_fernet_key", lambda: None)
    monkeypatch.setattr(server_mod, "validate_jwt_secret", lambda: None)
    monkeypatch.setattr(server_mod, "validate_inter_node_secret", lambda: None)
    monkeypatch.setattr(server_mod, "install_llm_gate", lambda app: None)
    monkeypatch.setattr(server_mod, "install_alert_handler", lambda: None)

    class _App:
        state = type("S", (), {"limiter": limiter})()

    before = _registrations()

    async def _drive():
        async with registered_globals():
            async with server_mod._lifespan(_App()):
                pass

    _run(_drive())

    assert "char" in flaky.roles(), f"关停时没补写，那条消息永久丢了：{flaky.wrote}"
    assert _registrations() == before, (
        "关停那一段把进程级注册留在了后面（缺陷 113）：注册仍指着已经关掉的 loop，"
        "后面任何投递都会落到死 loop 上")


# ═══════════════════════════════════════════════════════════════════════════════
# G1–G2：群聊这条链上的接线（缺陷 121 的回归面）
# ═══════════════════════════════════════════════════════════════════════════════

def test_G1_group_user_save_failure_still_enqueues_the_reply(flaky, owner):
    """用户那条写失败 → 角色那条**照样入队**，恢复后两笔按原顺序补上。

    改前形态（缺陷 121）：两笔共用一个 `nonfatal` 块，用户那条一失败，角色那条整段不执行 ——
    调用方只拿到一个 `failed`，无从分辨缺的是哪一条，而角色已经说完的话连队都没进。
    """
    gid, cards = _make_group(flaky, owner)
    client = _client(flaky, owner)

    flaky.fail_roles = {"user"}
    flaky.ping_ok = False
    body1 = _group_send(client, gid, cards[0], "第一句")
    assert body1["user_save"]["state"] == "pending", body1
    assert body1["char_save"]["state"] == "pending", (
        f"用户那条写失败把角色那条整段跳过了（缺陷 121）：{body1}")
    assert body1["user_msg_id"] is None and body1["char_msg_id"] is None
    k_user = body1["user_save"]["key"]
    k_char = body1["char_save"]["key"]

    flaky.fail_roles = set()
    flaky.ping_ok = True
    body2 = _group_send(client, gid, cards[0], "第二句")

    flushed = {f["key"]: f["id"] for f in body2["flushed"]}
    assert k_user in flushed and k_char in flushed, f"上一轮那两笔没补上：{body2['flushed']}"
    assert flushed[k_user] < flushed[k_char] < body2["user_msg_id"], (
        "补写的两笔顺序反了，或排到了本轮消息后面")

    rows = _run(flaky.get_group_messages(gid))
    assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant"], (
        f"读回来的历史不是用户看到的顺序：{rows}")


def test_G2_group_flush_endpoint_backfills_in_order(flaky, owner, intruder):
    """群聊的重试接口：属主调它真补上（按原顺序），非属主与「不存在」同码同文案。"""
    gid, cards = _make_group(flaky, owner)
    client = _client(flaky, owner)

    flaky.fail_roles = {"user"}
    flaky.ping_ok = False
    body1 = _group_send(client, gid, cards[0], "第一句")
    k_user = body1["user_save"]["key"]
    k_char = body1["char_save"]["key"]

    flaky.fail_roles = set()
    flaky.ping_ok = True
    r = client.post(f"/api/group/{gid}/flush", json={})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    assert [f["key"] for f in r.json()["flushed"]] == [k_user, k_char], (
        f"补写的不是队里那两笔、或顺序不对：{r.json()}")

    rows = _run(flaky.get_group_messages(gid))
    assert [row["role"] for row in rows] == ["user", "assistant"], rows

    intruder_client = _client(flaky, intruder)
    foreign = intruder_client.post(f"/api/group/{gid}/flush", json={})
    missing = intruder_client.post(f"/api/group/nope_{uuid.uuid4().hex}/flush", json={})
    assert foreign.status_code == 404, (
        f"非属主用他人的群聊调重试接口：{foreign.status_code} {foreign.text[:200]}")
    assert foreign.status_code == missing.status_code
    assert foreign.json()["detail"] == missing.json()["detail"], "非属主与不存在同码不同文案"


# ═══════════════════════════════════════════════════════════════════════════════
# O6：队列不持有存储实例（缺陷 117 的教训）—— 补写问的是**当下**的库
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# E1–E2：一轮以**错误帧**收尾时，本轮的补写报告照样送到前端
#
# done 帧带报告已经由 C1 / G1 锁住；错误帧原先只带 `error`。于是这一轮里顺路补写成功的
# 更早消息（后端已经给了它们真实行 id）在前端永远翻不成「已保存」—— 用户刷新才恢复。
# ═══════════════════════════════════════════════════════════════════════════════

def test_E1_error_frame_carries_this_rounds_backfill_report(flaky, owner):
    """一对一：这一轮以错误帧收尾时，本轮补写的读数不许丢。

    变异：错误帧只发 `_stream_error_payload(exc)`、不并 `report.as_json()` → 本条红。
    """
    sid = _new_sid()
    engine = _Engine()
    _install_session(flaky, sid, owner, engine)
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done = _done(_stream(client, sid, "第一句"))
    key = done["char_save"]["key"]
    assert done["char_save"]["state"] == "pending"

    # 本轮：库回来了 —— 写用户消息那一步顺路把上一轮那条补上；随后 LLM 抛错收尾。
    flaky.fail_roles = set()
    flaky.ping_ok = True

    def _boom(*_a, **_kw):
        raise RuntimeError("stream down")

    engine.chat_stream = _boom

    frames = _stream(client, sid, "第二句")
    err = [f for f in frames if "error" in f]
    assert len(err) == 1, f"没出错误帧，本用例没验到东西：{frames}"

    flushed = {f["key"]: f["id"] for f in err[0].get("flushed", [])}
    assert key in flushed, (
        f"错误帧没带本轮补写的报告 —— 那条已经落库，前端却永远停在未保存：{err[0]}")


def test_E2_group_error_frame_carries_this_rounds_backfill_report(flaky, owner):
    """群聊：同上，走广播那条流。"""
    gid, cards = _make_group(flaky, owner)
    client = _client(flaky, owner)

    flaky.fail_roles = {"assistant"}
    flaky.ping_ok = False
    body1 = _group_send(client, gid, cards[0], "第一句")
    key = body1["char_save"]["key"]
    assert body1["char_save"]["state"] == "pending", body1

    flaky.fail_roles = set()
    flaky.ping_ok = True

    import deps

    def _boom(*_a, **_kw):
        raise RuntimeError("broadcast down")

    deps.get_group_sessions()[gid].broadcast_stream = _boom

    frames = _group_frames(client, gid, "第二句", cards)
    err = [f for f in frames if "error" in f]
    assert len(err) == 1, f"没出错误帧，本用例没验到东西：{frames}"

    flushed = {f["key"]: f["id"] for f in err[0].get("flushed", [])}
    assert key in flushed, (
        f"广播的错误帧没带本轮补写的报告 —— 那条已经落库，前端却永远停在未保存：{err[0]}")


def test_O6_cleanup_flush_pings_the_storage_of_the_moment(flaky, store, owner, monkeypatch):
    """清理补写解析的是**调用时**的存储，不是入队时那个。

    `flush_outboxes`（清理循环与关停的唯一出口）每次调用现取 `get_storage()`；队列自己
    不持有存储实例。持有的话，存储被换掉（缺陷 117 的两个来源）之后，补写会一直去问
    那个已经作废的库，新的库上的消息永远补不上。
    """
    import deps

    sid = _new_sid()
    _install_session(flaky, sid, owner, _Engine())
    client = _client(flaky, owner)

    flaky.fail_roles = {"char"}
    flaky.ping_ok = False
    done = _done(_stream(client, sid, "第一句"))
    assert done["char_save"]["state"] == "pending"

    # 第一轮补写：当下的库就是这个替身（把任何「首用即缓存」的实现喂饱）
    _run(deps.flush_outboxes({sid: deps.get_sessions()[sid]}))
    stale_calls = flaky.ping_calls
    assert stale_calls > 0, "第一轮补写没问过库，本用例没验到东西"

    other = _FlakyStore(store)
    monkeypatch.setattr(deps, "_storage", other)

    _run_cleanup_once(monkeypatch)

    assert other.ping_calls > 0, "清理补写没去问当下的库 —— 它握着入队时那个存储实例"
    assert flaky.ping_calls == stale_calls, "清理补写还在问入队时那个库"
