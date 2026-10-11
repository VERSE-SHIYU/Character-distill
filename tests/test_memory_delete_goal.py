"""目标检查：永久删除存档 / 卡 / 群 / 书时，它的长期记忆跟着删；别的不受影响。

spec：`docs/specs/memory-delete-cascade.md` §5。真接口 + 真存储（sqlite）+ 真 mem0 加本地 qdrant；
embedding 用确定性替身，不调任何模型。每条检查都从用户真正点的那个接口进去。
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import uuid

os.environ.setdefault("MEM0_TELEMETRY", "False")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import mem0.embeddings.openai as _m0_embed  # noqa: E402
import mem0.llms.openai as _m0_llm  # noqa: E402
from pwdlib import PasswordHash  # noqa: E402

import core.embeddings as _embeddings  # noqa: E402
import deps  # noqa: E402
import server  # noqa: E402
from core.memory_manager import MemoryManager  # noqa: E402
from core.memory_scope import MemoryScope  # noqa: E402
from routers.auth import _create_access_token, get_jwt_secret  # noqa: E402
from storage.sqlite_store import SQLiteStore  # noqa: E402

DIM = 1024
_PW = PasswordHash.recommended().hash("Pass1234")


class _Embedder:
    def __init__(self, **_kw):
        pass

    @staticmethod
    def _vec(text: str) -> list[float]:
        v = [0.0] * DIM
        for t in text:
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, text, memory_action=None):
        return self._vec(text)

    def embed_batch(self, texts, memory_action="add"):
        return [self._vec(t) for t in texts]


class _Quiet:
    def __init__(self, **_kw):
        pass


class _UserLLM:
    model = "stub-user"

    def credential_fingerprint(self):
        return "fp-delete"

    def derive(self, **_kw):
        return self


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class Env:
    """一个用户 + 真存储 + 真记忆库 + 真接口。"""

    def __init__(self, tmp_path, monkeypatch):
        self.store = SQLiteStore(str(tmp_path / "del.db"))
        self.uid = f"usr_{uuid.uuid4().hex[:12]}"
        _run(self.store.create_user(self.uid, "删除检查用户", _PW))
        self.manager = MemoryManager({}, db_dir=tmp_path / "mem0_db")
        self.view = self.manager.for_user(user_id=self.uid, llm=_UserLLM(), embedding_key="sk-emb-x")
        monkeypatch.setattr(deps, "_storage", self.store)
        self.current_manager = self.manager          # 换成 None = 记忆系统没开
        server.app.dependency_overrides[deps.get_memory_manager] = lambda: self.current_manager
        deps.get_sessions().clear()
        self.client = TestClient(server.app, raise_server_exceptions=False)
        self.h = {"Authorization": f"Bearer {_create_access_token(self.uid, '删除检查用户', get_jwt_secret())}"}

    # ── 造数据 ──
    def text(self) -> str:
        tid = f"txt_{uuid.uuid4().hex[:12]}"
        _run(self.store.save_text(tid, "src.txt", "正文", user_id=self.uid))
        return tid

    def card(self, text_id: str | None = None) -> str:
        cid = f"card_{uuid.uuid4().hex[:12]}"
        name = f"角色{cid[-6:]}"                      # 同一本书里角色名不能重复
        assert _run(self.store.save_card(cid, text_id or self.text(), name, '{"name": "%s"}' % name, self.uid))
        return cid

    def archive(self, card_id: str) -> str:
        sid = uuid.uuid4().hex[:12]
        _run(self.store.save_session(sid, card_id, "user", "", user_id=self.uid))
        return sid

    def group(self, card_ids: list[str]) -> str:
        gid = f"grp_{uuid.uuid4().hex[:12]}"
        _run(self.store.create_group_session(gid, "群", card_ids, user_id=self.uid))
        return gid

    def remember(self, scope: MemoryScope, n: int) -> MemoryScope:
        for i in range(n):
            assert self.view.add_manual(f"记忆{uuid.uuid4().hex[:6]}{i}", scope)
        return scope

    def legacy(self, card_id: str, n: int) -> None:
        """隔离上线之前存下的旧记忆：只有卡、没有归属。"""
        for i in range(n):
            self.view._mem.add(f"旧记忆{uuid.uuid4().hex[:6]}{i}", user_id=card_id, infer=False)

    # ── 读 ──
    def count(self, scope: MemoryScope) -> int:
        return len(self.manager.get_all(scope))

    def whole(self, card_id: str) -> int:
        return self.count(MemoryScope.whole_card(card_id))

    def delete(self, url: str, expect: int = 200):
        r = self.client.delete(url, headers=self.h)
        assert r.status_code == expect, f"{url} → {r.status_code} {r.text}"
        return r

    def post(self, url: str, expect: int = 200):
        r = self.client.post(url, headers=self.h)
        assert r.status_code == expect, f"{url} → {r.status_code} {r.text}"
        return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(_m0_llm, "OpenAI", _Quiet)
    monkeypatch.setattr(_m0_embed, "OpenAI", _Quiet)
    monkeypatch.setattr(_embeddings, "Mem0BridgeEmbedder", _Embedder)
    e = Env(tmp_path, monkeypatch)
    try:
        yield e
    finally:
        server.app.dependency_overrides.pop(deps.get_memory_manager, None)


# ═══ D1 永久删一个存档：只清这个存档的 ═══════════════════════════════════════

@pytest.mark.parametrize("route", ["/api/history/{id}/permanent", "/api/history/{id}?permanent=true"])
def test_D1_deleting_an_archive_clears_only_that_archive(env, route):
    card = env.card()
    a, b = env.archive(card), env.archive(card)
    g = env.group([card])
    sa = env.remember(MemoryScope.archive(card, a), 3)
    sb = env.remember(MemoryScope.archive(card, b), 2)
    sg = env.remember(MemoryScope.group(card, g), 2)

    env.delete(f"/api/history/{a}")                       # 进回收站
    assert env.count(sa) == 3, "进回收站就把记忆清了（恢复后记忆回不来）"
    env.post(f"/api/history/{a}/restore")
    assert env.count(sa) == 3, "恢复后记忆不在了"

    env.delete(f"/api/history/{a}")
    env.delete(route.format(id=a))                        # 永久删除
    assert env.count(sa) == 0, "存档永久删除后它的记忆还在"
    assert env.count(sb) == 2, "删 A 把同一张卡的 B 存档的记忆也清了"
    assert env.count(sg) == 2, "删一对一存档把群里的记忆也清了"


# ═══ D2 清空回收站：回收站里每个存档的都清 ═══════════════════════════════════

def test_D2_purging_the_trash_clears_every_trashed_archive(env):
    card = env.card()
    a, b, c = env.archive(card), env.archive(card), env.archive(card)
    sa = env.remember(MemoryScope.archive(card, a), 2)
    sb = env.remember(MemoryScope.archive(card, b), 2)
    sc = env.remember(MemoryScope.archive(card, c), 2)
    env.delete(f"/api/history/{a}")
    env.delete(f"/api/history/{b}")

    r = env.delete("/api/history/trash/purge")
    assert r.json()["purged"] == 2
    assert env.count(sa) == 0 and env.count(sb) == 0, "清空回收站后存档的记忆还在"
    assert env.count(sc) == 2, "清空回收站把没删的存档的记忆也清了"
    assert _run(env.store.get_session_unscoped(a)) is None and _run(env.store.get_session_unscoped(b)) is None
    assert _run(env.store.get_session_unscoped(c)) is not None


# ═══ D3 永久删一张卡：它的全部记忆都清 ═══════════════════════════════════════

@pytest.mark.parametrize("route", ["/api/cards/{id}/permanent", "/api/cards/{id}/purge"])
def test_D3_deleting_a_card_clears_all_of_its_memories(env, route):
    card, other = env.card(), env.card()
    a = env.archive(card)
    g = env.group([card, other])
    env.remember(MemoryScope.archive(card, a), 2)
    env.remember(MemoryScope.group(card, g), 2)
    env.legacy(card, 2)
    so = env.remember(MemoryScope.archive(other, env.archive(other)), 2)
    sog = env.remember(MemoryScope.group(other, g), 1)
    assert env.whole(card) == 6

    env.delete(f"/api/cards/{card}")
    assert env.whole(card) == 6, "卡进回收站就把记忆清了"
    env.delete(route.format(id=card))
    assert env.whole(card) == 0, "卡永久删除后还有它的记忆（存档 / 群 / 旧记忆）"
    assert env.count(so) == 2 and env.count(sog) == 1, "删一张卡把别的卡的记忆也清了"


# ═══ D4 永久删一个群：只清这个群的 ═══════════════════════════════════════════

def test_D4_deleting_a_group_clears_only_that_group(env):
    c1, c2 = env.card(), env.card()
    g, h = env.group([c1, c2]), env.group([c1])
    s1g = env.remember(MemoryScope.group(c1, g), 2)
    s2g = env.remember(MemoryScope.group(c2, g), 2)
    s1h = env.remember(MemoryScope.group(c1, h), 2)
    s1a = env.remember(MemoryScope.archive(c1, env.archive(c1)), 2)

    env.delete(f"/api/group/{g}")
    assert env.count(s1g) == 2 and env.count(s2g) == 2, "群进回收站就把记忆清了"
    env.delete(f"/api/group/{g}/permanent")
    assert env.count(s1g) == 0 and env.count(s2g) == 0, "群永久删除后成员在这个群里的记忆还在"
    assert env.count(s1h) == 2, "删一个群把同一张卡在另一个群里的记忆也清了"
    assert env.count(s1a) == 2, "删群把成员的一对一记忆也清了"


# ═══ D5 永久删一本书：跟着被删的卡的记忆都清；留卡时不清 ═════════════════════

def test_D5_deleting_a_book_clears_the_memories_of_its_cards(env):
    book, other_book = env.text(), env.text()
    live, trashed, outside = env.card(book), env.card(book), env.card(other_book)
    for c in (live, trashed, outside):
        env.remember(MemoryScope.archive(c, env.archive(c)), 2)
    env.delete(f"/api/cards/{trashed}")                   # 书下面有一张卡已经在回收站

    env.delete(f"/api/text/{book}")
    assert env.whole(live) == 2 and env.whole(trashed) == 2, "书进回收站就把记忆清了"
    env.delete(f"/api/text/{book}/permanent")
    assert env.whole(live) == 0, "书永久删除后它的卡的记忆还在"
    assert env.whole(trashed) == 0, "书永久删除后，回收站里那张卡的记忆还在"
    assert env.whole(outside) == 2, "删一本书把别的书的卡的记忆也清了"


def test_D5b_deleting_a_book_but_keeping_cards_keeps_their_memories(env):
    book = env.text()
    card = env.card(book)
    s = env.remember(MemoryScope.archive(card, env.archive(card)), 2)
    env.delete(f"/api/text/{book}?keep_cards=true")
    env.delete(f"/api/text/{book}/permanent?keep_cards=true")
    assert _run(env.store.get_card_unscoped(card)) is not None, "前提破了：留卡删书后卡不在了"
    assert env.count(s) == 2, "留卡删书把卡的记忆清了"


def test_D5c_keeping_cards_only_at_the_permanent_step_keeps_their_memories(env):
    """书进回收站时**没**选留卡（卡还挂在书上），永久删除时才选留卡 —— 卡活下来，记忆也得在。

    D5b 两步都带 keep_cards：进回收站那一步就把卡摘走了，永久删除时书名下已经没有卡，
    测不到「永久删除这一步看不看 keep_cards」。这条才测得到。
    """
    book = env.text()
    card = env.card(book)
    s = env.remember(MemoryScope.archive(card, env.archive(card)), 2)
    env.delete(f"/api/text/{book}")                               # 不留卡：卡还挂在书上
    assert _run(env.store.get_card_unscoped(card))["text_id"] == book, "前提破了：卡已经不挂在书上"
    env.delete(f"/api/text/{book}/permanent?keep_cards=true")     # 这一步才选留卡
    assert _run(env.store.get_card_unscoped(card)) is not None, "前提破了：留卡删书后卡不在了"
    assert env.count(s) == 2, "永久删除时选了留卡，卡的记忆却被清了"


# ═══ D6 清不掉记忆就不删行；D7 记忆系统没开照样能删 ═════════════════════════

def test_D6_a_failed_cleanup_leaves_the_archive_in_the_trash(env, monkeypatch):
    card = env.card()
    a = env.archive(card)
    sa = env.remember(MemoryScope.archive(card, a), 2)
    env.delete(f"/api/history/{a}")

    real = env.manager.delete_all
    monkeypatch.setattr(env.manager, "delete_all", lambda scope: False)   # 记忆库这一刻删不动
    env.delete(f"/api/history/{a}/permanent", expect=500)
    row = _run(env.store.get_session_unscoped(a))
    assert row is not None and row.get("deleted_at"), "记忆没清掉，存档行却删了（记忆从此找不回来）"
    assert env.count(sa) == 2

    monkeypatch.setattr(env.manager, "delete_all", real)                  # 重试
    env.delete(f"/api/history/{a}/permanent")
    assert env.count(sa) == 0 and _run(env.store.get_session_unscoped(a)) is None


def test_D7_deleting_still_works_with_memory_switched_off(env):
    card = env.card()
    a = env.archive(card)
    env.delete(f"/api/history/{a}")
    env.current_manager = None
    env.delete(f"/api/history/{a}/permanent")
    assert _run(env.store.get_session_unscoped(a)) is None
