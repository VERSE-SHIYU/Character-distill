"""D25 第 2 步目标检查：同一账号的每次蒸馏尝试共用一只 AIMD 闸。

G1–G6、G8–G13 先以 ``xfail(strict=True)`` 落红，实现后摘标记转绿；
G7、G14 是对照（原实例聊天不过闸 / 没有账号身份的对象不包闸，现状即绿）。

判据全部是**行为**：闸的自报在途数、假上游的在途峰值、调用是否被排队。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import adapters.llm_adapter as M
import core.concurrency as C
from core.distiller import Distiller
from core.schema import CharacterCard
from core.text_manager import TextManager
from routers import distill as D


# ── 假上游：同步 + 异步共用一个在途计数，超过上限回 DeepSeek 形态的 429 ──────────

class _RateLimited(Exception):
    status_code = 429


class _Upstream:
    def __init__(self, limit: int, hold_s: float = 0.02) -> None:
        self.limit = limit
        self.hold_s = hold_s
        self._lock = threading.Lock()
        self._inflight = 0
        self.peak = 0
        self.calls = 0
        self.statuses: list[int] = []

    def enter(self) -> bool:
        with self._lock:
            self._inflight += 1
            self.peak = max(self.peak, self._inflight)
            self.calls += 1
            throttled = self._inflight > self.limit
            self.statuses.append(429 if throttled else 200)
            return throttled

    def leave(self) -> None:
        with self._lock:
            self._inflight -= 1


def _completion(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=None,
    )


def _stream_chunk(content: str | None = None, finish_reason: str | None = None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=None,
    )


class _FakeAsyncClient:
    def __init__(self, upstream: _Upstream, text: str = "ok") -> None:
        self._up = upstream
        self._text = text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        throttled = self._up.enter()
        try:
            await asyncio.sleep(self._up.hold_s)
            if throttled:
                raise _RateLimited("concurrency limit exceeded")
            return _completion(self._text)
        finally:
            self._up.leave()

    async def close(self) -> None:
        return None


class _FakeSyncClient:
    def __init__(self, upstream: _Upstream, text: str = "ok") -> None:
        self._up = upstream
        self._text = text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        throttled = self._up.enter()
        try:
            time.sleep(self._up.hold_s)
            if throttled:
                raise _RateLimited("concurrency limit exceeded")
            if kwargs.get("stream"):
                return iter([_stream_chunk("ok"), _stream_chunk(None, "stop")])
            return _completion(self._text)
        finally:
            self._up.leave()


def _adapter(base_url: str) -> M.LLMAdapter:
    return M.LLMAdapter(api_key="sk-fake-account", base_url=base_url, model="fake-model")


def _config(tmp_path, cap: int) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(f"distill:\n  map_concurrency: {cap}\n  chunk_size: 3000\n", encoding="utf-8")
    return str(path)


# ── G1：线程各自 asyncio.run + 同步线程，共用一只闸 ──────────────────────────

def test_g1_sync_and_async_share_one_gate():
    C.reset_account_gates()
    gate = C.account_gate("acct-g1", 3)
    seen: list[int] = []
    lock = threading.Lock()

    def record() -> None:
        with lock:
            seen.append(gate.inflight)

    def async_worker() -> None:
        async def run() -> None:
            for _ in range(10):
                async with gate:
                    record()
                    await asyncio.sleep(0.01)
        asyncio.run(run())

    def sync_worker() -> None:
        for _ in range(10):
            gate.acquire()
            try:
                record()
                time.sleep(0.01)
            finally:
                gate.release()

    threads = [threading.Thread(target=async_worker),
               threading.Thread(target=async_worker),
               threading.Thread(target=sync_worker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
        assert not t.is_alive()

    assert len(seen) == 30, f"只记到 {len(seen)} 次放行"
    assert max(seen) <= 3, f"在途峰值 {max(seen)} 超过闸上限 3"


# ── G2：同账号 3 个蒸馏任务（Map + 同步调用混合） ───────────────────────────

def test_g2_three_distills_share_the_account_gate(tmp_path, monkeypatch):
    C.reset_account_gates()
    monkeypatch.setattr(M, "_GEN_BACKOFF_S", 0.0)
    monkeypatch.setattr(M, "_STREAM_BACKOFF_S", 0.0)
    upstream = _Upstream(limit=5, hold_s=0.02)
    llm = _adapter("http://acct-g2.invalid/v1")
    llm._client = _FakeSyncClient(upstream)
    cfg = _config(tmp_path, 5)

    distillers = [Distiller(llm, config_path=cfg) for _ in range(3)]
    failures: list[BaseException] = []
    lock = threading.Lock()

    def worker(d: Distiller) -> None:
        try:
            # Map 与同步调用**并发**发出：否则同步调用总落在别人 Map 收尾之后，
            # 「同步不过闸」这个缺陷就露不出来（峰值永远差在闸内那几路之下）。
            def run_map() -> None:
                try:
                    client = _FakeAsyncClient(upstream)
                    asyncio.run(d._run_map_concurrent(
                        [f"第{i}片正文" for i in range(8)],
                        lambda chunk: ("你是角色分析专家", f"分析：{chunk}"),
                        "distill_map", client=client,
                    ))
                except BaseException as exc:
                    with lock:
                        failures.append(exc)

            t = threading.Thread(target=run_map)
            t.start()
            try:
                for _ in range(2):
                    d.llm.chat("sys", [{"role": "user", "content": "同步调用"}])
            finally:
                t.join(30)
        except BaseException as exc:
            with lock:
                failures.append(exc)

    threads = [threading.Thread(target=worker, args=(d,)) for d in distillers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
        assert not t.is_alive()

    assert failures == [], f"3 个任务有失败：{failures[:2]}"
    assert upstream.peak <= 5, f"账号在途峰值 {upstream.peak} 超过上限 5（闸没共享）"


# ── G3：取消不泄漏名额（含「取消时恰好已拿到名额」） ────────────────────────

def test_g3_cancelled_waiters_leave_no_permits():
    C.reset_account_gates()
    gate = C.account_gate("acct-g3", 2)
    gate.acquire()
    gate.acquire()

    async def cancel_waiters() -> None:
        tasks = [asyncio.create_task(gate.__aenter__()) for _ in range(5)]
        await asyncio.sleep(0.1)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(cancel_waiters())
    gate.release()
    gate.release()

    done = threading.Event()

    def reacquire() -> None:
        async def run() -> None:
            for _ in range(2):
                async with gate:
                    pass
        asyncio.run(run())
        done.set()

    t = threading.Thread(target=reacquire)
    t.start()
    assert done.wait(3), "取消后的等待者泄漏了名额：闸再也放不进人"


def test_g3b_cancel_after_acquire_returns_the_permit():
    """「源任务已完成、调用方却被取消」的竞争：名额必须补还。

    这条窗口在真实调度里只有一次 call_soon 宽，无法从外部稳定命中，故直接驱动
    ``AccountGate._return_if_acquired``（它在 ``__aenter__`` 的 CancelledError 分支里挂上）。
    """
    C.reset_account_gates()
    gate = C.AccountGate(1)
    fut = asyncio.run_coroutine_threadsafe(gate._gate.__aenter__(), gate._loop)
    fut.result(timeout=2)
    assert gate.inflight == 1

    gate._return_if_acquired(fut)

    deadline = time.monotonic() + 2
    while gate.inflight != 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gate.inflight == 0, "拿到名额后被取消，名额没有补还"


# ── G4：不同账号的闸互不影响 ──────────────────────────────────────────────

def test_g4_two_accounts_have_independent_ceilings():
    C.reset_account_gates()
    a = C.account_gate("acct-a", 10)
    b = C.account_gate("acct-b", 10)

    a.report_rate_limited(a.generation)

    assert a.ceiling == 7, f"429 没把 A 压下来：{a.ceiling}"
    assert b.ceiling == 10, "B 的上限被 A 的 429 带下去了"


# ── G5：同步 chat 等闸时长顺延总时限 ──────────────────────────────────────

def test_g5_sync_chat_extends_deadline_for_queueing(monkeypatch):
    C.reset_account_gates()
    monkeypatch.setattr(M, "_GEN_DEADLINE_S", 1.5)
    upstream = _Upstream(limit=99, hold_s=0.0)
    llm = _adapter("http://acct-g5.invalid/v1")
    llm._client = _FakeSyncClient(upstream)
    gate = C.account_gate("acct-g5", 1)

    gate.acquire()
    outcome: dict = {}

    def worker() -> None:
        try:
            outcome["value"] = llm.chat("sys", [{"role": "user", "content": "排队"}], gate=gate)
        except BaseException as exc:
            outcome["error"] = exc

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(2.0)          # 排队时长 > 总时限 1.5s
    gate.release()
    t.join(10)

    assert "error" not in outcome, f"排队吃掉了总时限：{outcome['error']}"
    assert outcome["value"] == "ok"


# ── G6：流式名额占到流读完 / 生成器关闭 ───────────────────────────────────

def test_g6_stream_holds_the_permit_until_consumed():
    C.reset_account_gates()
    upstream = _Upstream(limit=99, hold_s=0.0)
    llm = _adapter("http://acct-g6.invalid/v1")
    llm._client = _FakeSyncClient(upstream)
    gate = C.account_gate("acct-g6", 1)

    stream = llm.chat_stream_long(
        "sys", [{"role": "user", "content": "流"}], gate=gate)
    assert gate.inflight == 0
    assert next(stream) == "ok"
    assert gate.inflight == 1, "流未读完，名额已经还了"
    list(stream)
    assert gate.inflight == 0, "流读完后名额没有归还"

    early = llm.chat_stream_long(
        "sys", [{"role": "user", "content": "流"}], gate=gate)
    next(early)
    early.close()
    assert gate.inflight == 0, "生成器关闭后名额没有归还"


# ── G7：原实例的聊天入口不加闸（对照，现状即绿） ─────────────────────────

def test_g7_original_instance_chat_paths_are_not_gated():
    upstream = _Upstream(limit=99, hold_s=0.0)
    llm = _adapter("http://acct-g7.invalid/v1")
    llm._client = _FakeSyncClient(upstream)

    assert "gate" not in inspect.signature(M.LLMAdapter.chat_stream).parameters, (
        "chat_stream（聊天）不该接受闸参数")

    t0 = time.monotonic()
    assert llm.chat("sys", [{"role": "user", "content": "u"}]) == "ok"
    list(llm.chat_stream("sys", [{"role": "user", "content": "u"}]))
    assert time.monotonic() - t0 < 1.0, "原实例聊天调用被某个闸挡住了"


# ── G8：在家循环线程里调同步接口 → RuntimeError ───────────────────────────

def test_g8_sync_api_on_home_loop_thread_raises():
    C.reset_account_gates()
    gate = C.AccountGate(1)
    loop = C._gate_home_loop()
    errors: list[BaseException] = []
    done = threading.Event()

    def on_home() -> None:
        try:
            gate.acquire()
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    loop.call_soon_threadsafe(on_home)
    assert done.wait(2)
    assert errors and isinstance(errors[0], RuntimeError), (
        f"家循环线程里同步进闸没有硬失败：{errors}")


# ── G9：苏醒台词、开场白变体两处调用经过账号闸 ────────────────────────────

def test_g9a_awakening_waits_on_the_account_gate(tmp_path):
    C.reset_account_gates()
    upstream = _Upstream(limit=99, hold_s=0.0)
    llm = _adapter("http://acct-g9a.invalid/v1")
    llm._client = _FakeSyncClient(upstream, text="苏醒台词")
    d = Distiller(llm, config_path=_config(tmp_path, 1))
    gate = C.account_gate(llm.account_id, 1)
    card = CharacterCard(name="角色", first_message="你好")

    gate.acquire()
    outcome: dict = {}

    def worker() -> None:
        outcome["value"] = D._generate_awakening(d.llm, card)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.25)
    assert upstream.calls == 0, "闸满时苏醒台词没有排队（仍走原实例）"
    gate.release()
    t.join(5)

    assert outcome["value"] == "苏醒台词"
    assert upstream.calls == 1


def test_g9b_opening_variation_waits_on_the_account_gate(tmp_path):
    C.reset_account_gates()
    upstream = _Upstream(limit=99, hold_s=0.0)
    llm = _adapter("http://acct-g9b.invalid/v1")
    llm._client = _FakeSyncClient(upstream, text="开场白变体")
    d = Distiller(llm, config_path=_config(tmp_path, 1))
    gate = C.account_gate(llm.account_id, 1)
    card = CharacterCard(name="角色", first_message="你好")

    class _Storage:
        async def get_text_owned(self, text_id, user_id):
            return {"content": "正文"}

        async def list_cards(self, text_id, user_id):
            return [{"id": "c1", "name": "角色", "card_json": card.model_dump_json()}]

        async def save_session(self, *args, **kwargs):
            return None

    storage = _Storage()
    tm = TextManager(lambda: storage, d, object(), {},
                     indexing_service=None, memory_manager=None)

    async def _no_chars(*args, **kwargs):
        return []

    tm._build_all_characters = _no_chars
    tm.memory_for = lambda *args, **kwargs: None
    tm._create_session = lambda *args, **kwargs: "sess"
    tm._session_rag = lambda *args, **kwargs: None

    gate.acquire()
    outcome: dict = {}

    def worker() -> None:
        outcome["value"] = asyncio.run(tm.get_or_distill("t1", "角色", "u1"))

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.25)
    assert upstream.calls == 0, "闸满时开场白变体没有排队（仍走原实例）"
    gate.release()
    t.join(5)

    assert outcome["value"]["first_message"] == "开场白变体"


# ── G10：同一账号两次构造 Distiller 拿到同一个闸 ──────────────────────────

def test_g10_same_account_reuses_the_learned_gate(tmp_path):
    C.reset_account_gates()
    cfg = _config(tmp_path, 10)
    llm1 = _adapter("http://acct-g10.invalid/v1")
    llm2 = _adapter("http://acct-g10.invalid/v1")
    assert llm1.account_id == llm2.account_id

    d1 = Distiller(llm1, config_path=cfg)
    gate = C.account_gate(llm1.account_id, 10)
    assert d1.llm._gate is gate

    gate.report_rate_limited(gate.generation)
    learned = gate.ceiling
    assert learned < 10

    d2 = Distiller(llm2, config_path=cfg)
    assert d2.llm._gate is gate, "同一账号两次构造各建了一只闸"
    assert d2.llm._gate.ceiling == learned, "第二轮没有从第一轮学到的上限起步"


# ── G11：按**账号身份**包闸，不按对象类型（__getattr__ 转发包装器） ─────────────

def test_g11_wrapping_is_by_account_identity_not_type(tmp_path):
    """照 `scripts/replay_distill.py` 的 `_RecordingLLM`：真适配器外面套一层转发包装。"""
    C.reset_account_gates()
    llm = _adapter("http://acct-g11.invalid/v1")

    class _Forwarding:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

    wrapped = _Forwarding(llm)
    assert not isinstance(wrapped, M.LLMAdapter)

    cfg = _config(tmp_path, 4)
    d_wrapped = Distiller(wrapped, config_path=cfg)
    d_direct = Distiller(llm, config_path=cfg)

    assert isinstance(d_wrapped.llm, C.GatedLLM), "类型不是 LLMAdapter 的转发包装器漏过了闸"
    assert isinstance(d_direct.llm, C.GatedLLM)
    assert d_wrapped.llm.gate is d_direct.llm.gate, "包装器与直接适配器拿到的不是同一只账号闸"


# ── G12：select_by_schema 也过闸（闸满排队 + 429 下调） ─────────────────────

_TOOL = {
    "name": "pick_dialogue_examples",
    "description": "挑示例",
    "parameters": {"type": "object",
                   "properties": {"slot_1": {"type": "integer"}},
                   "required": ["slot_1"]},
}


def _tool_completion(args: dict):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(
                    function=SimpleNamespace(arguments=json.dumps(args)))],
            ),
            finish_reason="stop",
        )],
        usage=None,
    )


class _ToolClient:
    """同步假客户端：在途超上限 → DeepSeek 形态 429；否则回一个工具调用。"""

    def __init__(self, upstream: _Upstream, args: dict | None = None) -> None:
        self._up = upstream
        self._args = args if args is not None else {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        throttled = self._up.enter()
        try:
            if throttled:
                raise _RateLimited("concurrency limit exceeded")
            return _tool_completion(self._args)
        finally:
            self._up.leave()


def test_g12_select_by_schema_waits_on_the_gate_and_429_downscales(monkeypatch, tmp_path):
    C.reset_account_gates()

    # 走蒸馏实际会拿到的那只代理：若代理漏了 select_by_schema 的转发，请求就不带闸。
    llm = _adapter("http://acct-g12.invalid/v1")
    upstream = _Upstream(limit=99, hold_s=0.0)
    llm._client = _ToolClient(upstream, args={"slot_1": 3})
    proxy = Distiller(llm, config_path=_config(tmp_path, 1)).llm
    gate = proxy.gate

    gate.acquire()
    outcome: dict = {}

    def worker() -> None:
        outcome["value"] = proxy.select_by_schema(
            "sys", [{"role": "user", "content": "挑"}], _TOOL)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.25)
    assert upstream.calls == 0, "闸满时 select_by_schema 没有排队"
    gate.release()
    t.join(5)
    assert outcome["value"] == {"slot_1": 3}

    # 429：闸乘性下调（把 429 次数上限压到 1，首撞即耗尽，不必真睡退避）
    monkeypatch.setattr(M, "_RATE_LIMIT_ATTEMPTS", 1)
    throttled = _adapter("http://acct-g12b.invalid/v1")
    throttled._client = _ToolClient(_Upstream(limit=0))
    proxy2 = Distiller(throttled, config_path=_config(tmp_path, 10)).llm
    with pytest.raises(M.UpstreamFailure):
        proxy2.select_by_schema(
            "sys", [{"role": "user", "content": "挑"}], _TOOL)
    assert proxy2.gate.ceiling < 10, f"429 没把闸压下来：{proxy2.gate.ceiling}"


# ── G13：形态守卫 —— 蒸馏流用到的每个模型方法都必须在带闸代理上显式定义 ───────

# 非请求方法：建客户端不算「一次模型调用」，没有可排队的东西。
_NOT_LLM_REQUESTS = {"_make_async_client"}


def _gated_method_names() -> set[str]:
    """`GatedLLM` 类体内显式定义的方法名 —— 代理上定义的每个方法都带闸。"""
    tree = ast.parse(inspect.getsource(C.GatedLLM))
    cls = tree.body[0]
    return {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _llm_method_calls(rel: str) -> list[tuple[str, int]]:
    """文件里所有「接收者是 llm / *_llm」的方法调用 → [(方法名, 行号)]。

    `asyncio.to_thread(llm.chat, …)` 这类把方法**当值**交出去的写法同样算 —— 进了线程池
    照样发请求、照样要过闸，只扫 `func` 位置的调用会把它漏掉。
    """
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
    out: set[tuple[str, int]] = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        values = [n.func, *n.args, *(kw.value for kw in n.keywords)]
        for v in values:
            if not isinstance(v, ast.Attribute):
                continue
            recv = v.value
            name = (recv.attr if isinstance(recv, ast.Attribute)
                    else recv.id if isinstance(recv, ast.Name) else None)
            if name is None or not (name == "llm" or name.endswith("_llm")):
                continue
            if v is not n.func and not callable(
                    inspect.getattr_static(M.LLMAdapter, v.attr, None)):
                continue  # 参数位上的属性读（如 `llm.model`）不是方法，不算调用
            out.add((v.attr, v.lineno))
    return sorted(out, key=lambda item: item[1])


def test_g13_every_model_method_used_by_distill_is_gated():
    gated = _gated_method_names()
    assert gated, "GatedLLM 上没有显式方法 —— 守卫推导坏了，在假绿"
    allowed = gated | _NOT_LLM_REQUESTS

    offences: list[tuple[str, str, int]] = []
    for rel in ("core/distiller.py", "web/routers/distill.py", "core/text_manager.py"):
        calls = _llm_method_calls(rel)
        if rel != "web/routers/distill.py":
            assert calls, f"{rel} 里没扫到任何 llm 方法调用 —— 接收者识别可能扫错了"
        offences += [(rel, name, ln) for name, ln in calls if name not in allowed]

    assert not offences, (
        "蒸馏流里有模型方法没经过带闸代理（新增调用必须同时加进 GatedLLM 并带 gate=）："
        + ", ".join(f"{rel}:{ln} .{name}()" for rel, name, ln in offences))


# ── G14：没有账号身份的对象不包闸（MagicMock / 鸭子替身） ────────────────────

def test_g14_objects_without_account_identity_are_not_wrapped(tmp_path):
    from unittest.mock import MagicMock

    C.reset_account_gates()
    cfg = _config(tmp_path, 3)
    assert not isinstance(Distiller(MagicMock(), config_path=cfg).llm, C.GatedLLM), \
        "MagicMock 的 account_id 不是 str，不该包闸"

    class _Duck:
        def chat(self, *_a, **_kw):
            return "ok"

    assert not isinstance(Distiller(_Duck(), config_path=cfg).llm, C.GatedLLM), \
        "鸭子替身没有账号身份，不该包闸"
