# -*- coding: utf-8 -*-
"""④ 段 0a · 第 1 步：长期记忆按存档隔离 —— 目标检查（只加检查，不改实现）。

三个用户能感觉到的事实（spec §1）：同一张卡的两个存档互不可见；群聊与该卡的一对一互不可见；
升级前留下的旧记忆（没有存档标记）谁都读不到。

目标检查要求「真的」：

- 读写都经过真实的 ``ChatEngine`` 及其带的评估管道、事件服务、反思服务、``ContextEngine``
  —— 不直接调 ``MemoryView`` 的方法来代替引擎；
- 隔离由真的 ``mem0ai==2.0.20`` 加本地 qdrant（临时目录）来过滤 —— 不自己写假过滤器。

替身只用在本步允许处：聊天模型、评估模型、embedding、mem0 提炼 LLM。不联网，不调付费模型。

本文件在 main（实现未改）上：拒绝侧应红 —— 那时读的是同一张卡的**全部**记忆，别的存档
读得到本存档的东西。通过侧本来就绿，逐条说明了理由（防巧合的绿）。

G1、G2 覆盖 §3-F2 的全部 8 个读写点：4 个写入点各产生一条记忆；4 个读取点各读一次。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass

# 必须在 import mem0 **之前**：mem0 在 import 期读这个变量（memory/telemetry.py）。
os.environ.setdefault("MEM0_TELEMETRY", "False")

import pytest  # noqa: E402

import core.concurrency as _concurrency  # noqa: E402
import core.embeddings as _embeddings  # noqa: E402
import mem0.embeddings.openai as _m0_embed  # noqa: E402
import mem0.llms.openai as _m0_llm  # noqa: E402
from affinity_verdict import verdict  # noqa: E402
from core.chat_engine import ChatEngine  # noqa: E402
from core.memory_manager import MemoryManager  # noqa: E402
from core.schema import CharacterCard  # noqa: E402

DIM = 1024
USER = "usr-archive-scope"
CARD = CharacterCard(name="角色", identity="隔离检查用角色")


# ── 替身：embedding / mem0 自带提供方 / 三个模型 ───────────────────────────────

class _CharBagEmbedder:
    """确定性的字符袋向量：共享字符越多、余弦越高（mem0 的相似度门控才判得动）。

    同形于生产的 ``Mem0BridgeEmbedder``（``embed`` / ``embed_batch``）。用 md5 而非
    内置 ``hash`` —— 后者受 PYTHONHASHSEED 影响，跨进程不稳。
    """

    def __init__(self, **_kw):
        pass

    @staticmethod
    def _vec(text: str) -> list[float]:
        toks = list(text) + [text[i:i + 2] for i in range(max(0, len(text) - 1))]
        v = [0.0] * DIM
        for t in toks:
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, text: str, memory_action: str | None = None) -> list[float]:
        return self._vec(text)

    def embed_batch(self, texts: list[str], memory_action: str = "add") -> list[list[float]]:
        return [self._vec(t) for t in texts]


class _QuietOpenAI:
    """mem0 两条自带提供方在建 Memory 时各构造一个客户端 —— 换成不打网络的水货。"""

    def __init__(self, **_kw):
        pass


class _ExtractorLLM:
    """mem0 提炼 LLM 替身：从提炼 prompt 里读回暗号标签，产出确定性记忆文本。

    标签由**对话内容**携带（``<A>``），故同一份共享的提炼适配器能给不同存档产出各自
    可辨的记忆 —— 不是按调用次序瞎编。
    """

    def __init__(self) -> None:
        self.model = "stub-extract"
        self._n = 0

    def chat(self, system, messages, response_format=None, **_kw):
        self._n += 1
        prompt = " ".join(str(m.get("content", "")) for m in messages)
        # 提炼 prompt 里「## New Messages」排在历史之后，取**最后一个**标签才是这一轮的
        # 暗号 —— 取第一个会读到历史里上一轮/别的对话留下的标签。
        tags = re.findall(r"<(\w+)>", prompt)
        tag = tags[-1] if tags else "?"
        return json.dumps({"memory": [{"text": f"存档{tag}的秘密第{self._n}号"}]}, ensure_ascii=False)


class _UserLLM:
    """记忆的**用户**连接（供 ``MemoryManager.for_user`` 取凭据指纹 + 派生提炼适配器）。"""

    def __init__(self) -> None:
        self.model = "stub-user"

    def credential_fingerprint(self) -> str:
        return "fp-user-archive-scope"

    def derive(self, **_kw) -> _ExtractorLLM:
        return _ExtractorLLM()


class _EngineLLM:
    """引擎连接替身：评估（返回含 time_event 的 JSON）/ 反思（返回一条洞察）两用。

    每个存档一个实例，带自己的标签 —— 评估抽出的时间事件、反思写回的洞察都带标签，
    读取侧才分得出「这条记忆是哪来的」。
    """

    def __init__(self, tag: str) -> None:
        self._tag = tag
        self._evals = 0

    @property
    def model(self) -> str:
        return "stub-chat"

    def preflight(self) -> None:
        return None

    def chat(self, system: str, messages, **_kw) -> str:
        if "JSON" in system:
            self._evals += 1
            # 前两轮各抽一个时间事件：一个留给「已问过」标记（W3），另一个留作未问的
            # 到期事件供读取点 R3 读到 —— 标记与读取不能抢同一个事件。
            return _eval_reply(self._tag, event_index=self._evals if self._evals <= 2 else 0)
        if "反思" in system:
            return f"{self._tag}反思：我和对方在慢慢靠近，这份在意值得记下"
        return "好的"

    def chat_stream(self, *_a, **_kw):
        yield "好的"

    async def achat(self, *_a, **_kw) -> str:
        return "好的"


# 两个到期时间事件：近期（due_at 更晚）被「已问过」标记吃掉，早期（更早）留作 R3 读取。
_EVENTS = {
    1: ("早期事", "2020-01-01T00:00"),
    2: ("近期事", "2020-06-01T00:00"),
}


def _eval_reply(tag: str, *, event_index: int) -> str:
    data = dict(verdict(5))
    event = None
    if event_index in _EVENTS:
        suffix, due = _EVENTS[event_index]
        event = {"event": f"{tag}{suffix}", "when_text": "刚才", "due_at": due}
    data.update({
        "trust": 45, "mood": "开心", "guard": 35,
        "inner_voice": f"嗯，{tag}", "mood_emoji": "😊",
        "importance": 10,   # ≥7 的高质量素材，反思才攒得够
        "time_event": event,
        "in_character": 90, "ooc_reason": "", "assertion_confidence": 90,
    })
    return json.dumps(data, ensure_ascii=False)


# ── 夹具 ────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(_m0_llm, "OpenAI", _QuietOpenAI)
    monkeypatch.setattr(_m0_embed, "OpenAI", _QuietOpenAI)
    monkeypatch.setattr(_embeddings, "Mem0BridgeEmbedder", _CharBagEmbedder)


@pytest.fixture
def threads(monkeypatch):
    """收下每次经 ``ctx_thread`` 起的后台线程，供 ``_drain`` 等它们写完（F6）。"""
    started: list = []
    real = _concurrency.ctx_thread

    def spy(target, *args, **kwargs):
        t = real(target, *args, **kwargs)
        orig_start = t.start

        def _start():
            started.append(t)
            orig_start()

        t.start = _start
        return t

    monkeypatch.setattr(_concurrency, "ctx_thread", spy)
    return started


@pytest.fixture
def manager(tmp_path):
    return MemoryManager({}, db_dir=tmp_path / "mem0_db")


def _drain(started: list) -> None:
    while started:
        started.pop().join(timeout=30)


# ── 引擎与读写驱动 ──────────────────────────────────────────────────────────

def _engine(view, tag: str, card_id: str, *, session_id: str = "", group_id: str = "") -> ChatEngine:
    return ChatEngine(
        llm=_EngineLLM(tag), rag=None, card=CARD, memory_manager=view, card_id=card_id,
        storage=None, session_id=session_id, group_id=group_id, is_new_session=True,
    )


def _turn(engine: ChatEngine, threads: list, tag: str, n: int) -> None:
    engine.post_stream_process(f"我的暗号是<{tag}>，这是第{n}件事", f"{tag}的回复{n}")
    _drain(threads)


def _write_all(engine: ChatEngine, threads: list, tag: str) -> None:
    """让 §3-F2 的 4 个写入点各自产生一条记忆。

    - W1 每轮入库：三轮对话（第一轮的评估还带 time_event → W2 时间事件）
    - W4 反思洞察：把反思累加器顶到阈值再触发一次
    - W3 时间事件「已问过」标记：先让事件服务认领到期事件，再标记
    """
    for n in (1, 2, 3):
        _turn(engine, threads, tag, n)
    _force_reflect(engine, threads)
    engine._event_service.build_candidate_block()   # 认领到期事件 → 置 _injected_event_id
    engine._event_service.mark_asked()              # W3
    _drain(threads)


def _force_reflect(engine: ChatEngine, threads: list) -> bool:
    """把反思累加器顶到阈值，调一次 ``maybe_reflect``（读 R4 + 可能写 W4）。"""
    rs = engine._reflection_service
    rs._importance_acc = 10 ** 6
    rs._rounds_since_reflect = 10 ** 6
    triggered = rs.maybe_reflect(10, engine.llm, engine.card.name, storage=None)
    _drain(threads)
    return triggered


@dataclass
class Reads:
    r1: list[str]        # 检索进 prompt（context_engine:567）：命中的记忆文本
    r2: str              # 重逢问候取记忆（chat_engine:1557）
    r3: dict | None      # 取到期事件（event_service:40）
    r4: bool             # 反思取近期记忆（reflection_service:65）：是否找到了足够素材


def _read_all(engine: ChatEngine, threads: list, query: str) -> Reads:
    r1 = [it.text for it in engine._ctx_engine._retrieve_memories_ex(query).items]
    r2 = engine._build_reunion_memories()
    r3 = engine._event_service.get_due_event()
    r4 = _force_reflect(engine, threads)
    return Reads(r1=r1, r2=r2, r3=r3, r4=r4)


def _has(text: str, marker: str) -> bool:
    return marker in text


# ═══════════════════════════════════════════════════════════════════════════════
# G1 同卡两个存档 A、B
# ═══════════════════════════════════════════════════════════════════════════════

def test_G1_memories_stay_in_their_own_archive(manager, threads):
    view = manager.for_user(user_id=USER, llm=_UserLLM(), embedding_key="sk-emb-x")
    assert view is not None
    a = _engine(view, "A", "card-g1", session_id="arch-A")
    b = _engine(view, "B", "card-g1", session_id="arch-B")

    _write_all(a, threads, "A")
    ra = _read_all(a, threads, "存档A")
    rb = _read_all(b, threads, "存档A")

    # 通过侧：A 读得到自己在 A 里产生的记忆（main 上本来就绿 —— 见报告）。
    assert any(_has(t, "存档A") for t in ra.r1), f"A 检索不到自己的记忆：{ra.r1}"
    assert _has(ra.r2, "存档A"), f"A 重逢取不到自己的记忆：{ra.r2!r}"
    assert ra.r3 is not None and _has(ra.r3["event"], "A"), f"A 取不到自己的到期事件：{ra.r3}"
    assert ra.r4 is True, "A 的反思取不到自己的高质量素材"

    # 拒绝侧：B 读不到 A 的记忆（main 上应红）。
    assert not any(_has(t, "存档A") for t in rb.r1), f"B 检索到了 A 的记忆：{rb.r1}"
    assert not _has(rb.r2, "存档A"), f"B 重逢取到了 A 的记忆：{rb.r2!r}"
    assert rb.r3 is None or not _has(rb.r3["event"], "A"), f"B 取到了 A 的到期事件：{rb.r3}"
    assert rb.r4 is False, "B 的反思读到了 A 的素材"


# ═══════════════════════════════════════════════════════════════════════════════
# G2 群聊与一对一（同一张卡）
# ═══════════════════════════════════════════════════════════════════════════════

def test_G2a_group_memories_do_not_leak_into_the_one_on_one(manager, threads):
    view = manager.for_user(user_id=USER, llm=_UserLLM(), embedding_key="sk-emb-x")
    grp = _engine(view, "G", "card-g2a", group_id="grp-1")
    one = _engine(view, "S", "card-g2a", session_id="sess-1")

    _write_all(grp, threads, "G")
    rg = _read_all(grp, threads, "存档G")
    ro = _read_all(one, threads, "存档G")

    assert any(_has(t, "存档G") for t in rg.r1) and _has(rg.r2, "存档G")
    assert rg.r3 is not None and _has(rg.r3["event"], "G")
    assert rg.r4 is True

    assert not any(_has(t, "存档G") for t in ro.r1), f"一对一读到了群里的记忆：{ro.r1}"
    assert not _has(ro.r2, "存档G"), f"一对一重逢取到了群里的记忆：{ro.r2!r}"
    assert ro.r3 is None or not _has(ro.r3["event"], "G")
    assert ro.r4 is False, "一对一的反思读到了群里的素材"


def test_G2b_one_on_one_memories_do_not_leak_into_the_group(manager, threads):
    view = manager.for_user(user_id=USER, llm=_UserLLM(), embedding_key="sk-emb-x")
    one = _engine(view, "S", "card-g2b", session_id="sess-2")
    grp = _engine(view, "G", "card-g2b", group_id="grp-2")

    _write_all(one, threads, "S")
    ro = _read_all(one, threads, "存档S")
    rg = _read_all(grp, threads, "存档S")

    assert any(_has(t, "存档S") for t in ro.r1) and _has(ro.r2, "存档S")
    assert ro.r3 is not None and _has(ro.r3["event"], "S")
    assert ro.r4 is True

    assert not any(_has(t, "存档S") for t in rg.r1), f"群读到了一对一的记忆：{rg.r1}"
    assert not _has(rg.r2, "存档S"), f"群重逢取到了一对一的记忆：{rg.r2!r}"
    assert rg.r3 is None or not _has(rg.r3["event"], "S")
    assert rg.r4 is False, "群的反思读到了一对一的素材"


# ═══════════════════════════════════════════════════════════════════════════════
# G3 没有存档标记的旧记忆
# ═══════════════════════════════════════════════════════════════════════════════

def test_G3_legacy_unscoped_memories_are_readable_nowhere(manager, threads):
    view = manager.for_user(user_id=USER, llm=_UserLLM(), embedding_key="sk-emb-x")
    # 旧记忆 = 升级前的写法：只按卡（user_id）写入，不带存档标记（run_id）。
    view._mem.add([{"role": "user", "content": "旧记忆：没有存档标记"}],
                  user_id="card-g3", infer=False)

    engines = [
        _engine(view, "A", "card-g3", session_id="arch-A3"),
        _engine(view, "B", "card-g3", session_id="arch-B3"),
        _engine(view, "G", "card-g3", group_id="grp-3"),
    ]
    for eng in engines:
        r = _read_all(eng, threads, "旧记忆")
        assert not any(_has(t, "旧记忆") for t in r.r1), f"{eng._session_id or eng._group_id} 检索到旧记忆"
        assert not _has(r.r2, "旧记忆"), f"{eng._session_id or eng._group_id} 重逢取到旧记忆"
        assert r.r3 is None, f"{eng._session_id or eng._group_id} 取到旧事件"
        assert r.r4 is False, f"{eng._session_id or eng._group_id} 的反思读到旧记忆"


# ═══════════════════════════════════════════════════════════════════════════════
# G4 换记忆视图（set_memory）之后
# ═══════════════════════════════════════════════════════════════════════════════

def test_G4_scope_survives_a_memory_view_swap(manager, threads):
    user = _UserLLM()
    view = manager.for_user(user_id=USER, llm=user, embedding_key="sk-emb-x")
    a = _engine(view, "A4", "card-g4", session_id="arch-A4")
    b = _engine(view, "B4", "card-g4", session_id="arch-B4")

    _turn(b, threads, "B4", 1)                 # B 存档里也有东西
    _write_all(a, threads, "A4")

    # 用户存了新 key → deps 给活会话发一个新视图（同一份向量库，另一个 Memory 实例）。
    view2 = manager.for_user(user_id=USER, llm=user, embedding_key="sk-emb-y")
    assert view2 is not view
    a.set_memory(view2)

    ra = _read_all(a, threads, "存档A4")

    assert any(_has(t, "存档A4") for t in ra.r1), f"换视图后 A 检索不到自己的记忆：{ra.r1}"
    assert _has(ra.r2, "存档A4"), "换视图后 A 重逢取不到自己的记忆"
    assert not any(_has(t, "存档B4") for t in ra.r1), f"换视图后 A 检索到了 B 的记忆：{ra.r1}"
    assert not _has(ra.r2, "存档B4"), "换视图后 A 重逢取到了 B 的记忆"
