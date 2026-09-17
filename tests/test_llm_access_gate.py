# -*- coding: utf-8 -*-
"""锁：LLM 访问门收敛（调用点 geo 门 + 唯一解析出口）—— spec v2 §4 的 L1–L11。

**全部 `xfail(strict=True)`。** 这是 red-first 的形态：判据先落，本步（C1）只写锁、
不改生产代码，故这些用例现在**必须都是红的**。strict 让「红→绿」这件事有声响 ——
某一步把某条做绿了，它立刻以 **XPASS** 报错，逼人把标记摘掉；
标记没摘就说明这条还没真绿，不会被静默当成通过（§四「豁免即静默放行」的反面）。

每条 reason 写明**由哪一步翻转**：C2 上下文层 / C3 扼流点+出口 / C4 解析收敛 / C5 结构锁。
命题与归属见 spec §4 那张表，此处不重抄。

**这批锁集体要证的事**：请求之外拿不到 LLM 上下文就 fail-closed；请求之内任何一条
线程/任务派生都还带着请求身份 —— 于是「哪条路能到出站方法」不再靠人工普查维持
（`tests/census_llm_call_contexts.py` 是取数工具，不是判据）。

**边界（写清楚免得被当成漏洞）**：
  - 不测真 LLM 网络。凡涉出站的用例都把适配器的 `_client` 换成**毒对象**（一碰即失败），
    证的是「请求在出站前就被挡下」，不是「挡下之后恰好也没发出去」。
  - L8 的端点/流式两条走的是**非 `/api/` 公开路径**（免 JWT）：那正是 spec「匿名请求的
    user_id 为空串」那句所指的形状。带身份的路径由 L4 覆盖。
  - L10 的「无参」子句与 spec §5 的 `market.at_reply` 冲突（那一处是无参构造且被列为
    「只报告不修」）—— 见该用例 docstring，需要裁定。
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

import deps
import server
from adapters.llm_adapter import LLMAdapter
from conftest import TEST_JWT_SECRET
from deps import get_storage
from routers.auth import get_current_user
from routers.chat import router as chat_router
from routers.distill import router as distill_router
from storage.sqlite_store import SQLiteStore

_BLOCKED_IP = "1.1.1.1"
_ALLOWED_IP = "8.8.8.8"


# ── 新符号一律在用例体内导入 ──────────────────────────────────────────────
# C2/C3 才建 `web/llm_access.py`；模块级 import 会让整个文件**收集期报错**
# （十几条一起消失，看上去像「没写过」），而不是一批可读的红。故走函数内导入。

def _access():
    """`web/llm_access.py`：策略层（Source / resolve_llm / geo_verdict / 哨兵 / contextvar）。"""
    import importlib

    return importlib.import_module("web.llm_access")


def _llm_adapter_module():
    import importlib

    return importlib.import_module("adapters.llm_adapter")


def _caller_ip() -> str | None:
    """当前上下文里的调用方 IP —— 读不到上下文返回 None（不伪造默认值）。"""
    caller = _access().LLM_CALLER.get(None)
    return getattr(caller, "ip", None)


class _Poison:
    """毒客户端：任何属性访问都让用例失败（= 出站真的发生了）。"""

    def __getattr__(self, name: str):
        raise AssertionError(f"出站客户端被触碰了：.{name} —— 门没挡住")


def _poison(adapter: LLMAdapter) -> LLMAdapter:
    adapter._client = _Poison()
    adapter._async_client = _Poison()
    return adapter


def _fake_geo(monkeypatch, blocked: str = _BLOCKED_IP) -> None:
    """确定性的 geo 判定，**保持真规则的两维**：境内 IP **且** 非白名单 URL 才拦。

    一维化（「这个 IP 一律拦」）会让 L4 退化成测「解析时的提前 403」——
    而 L4 要证的是**调用点门**能拦住一个已经解析过、缓存在会话里的陈旧实例。

    两个绑定点都打 —— `geo_verdict` 既可能 `import web.geo_guard as G` 再 `G.check_api_allowed`，
    也可能 `from web.geo_guard import check_api_allowed`；锁不该锁定那一步选哪种写法。
    """
    import web.geo_guard as G

    def verdict(ip, url):
        if ip != blocked or "deepseek" in (url or ""):
            return True, ""
        return False, "境内不支持境外模型"

    monkeypatch.setattr(G, "check_api_allowed", verdict)
    try:
        A = _access()
    except ImportError:
        return
    if hasattr(A, "check_api_allowed"):
        monkeypatch.setattr(A, "check_api_allowed", verdict)


# ── L1 / L2：`/start` 的全局兜底，以及兜底实例的同一性 ────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "llm_gate.db"))


@pytest.fixture
def user_id():
    return f"u_gate_{uuid.uuid4().hex[:8]}"


def _distill_app(store, uid) -> FastAPI:
    app = FastAPI()
    app.include_router(distill_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "t", "is_admin": False,
    }
    return app


def _seed_text(store, uid, body: str = "角色甲说道：" * 30) -> str:
    tid = "txt_gate"
    asyncio.run(store.save_text(tid, "src.txt", body, user_id=uid))
    return tid


def _capture_thread(monkeypatch) -> list[tuple]:
    """把 `T.ctx_thread` 换成「只记参数、不起线程」—— `/start` 的入参由此取得。"""
    captured: list[tuple] = []

    def _fake_ctx_thread(target, args=(), kwargs=None, **kw):
        captured.append((target, args, kwargs or {}))
        return threading.Thread()

    monkeypatch.setattr("core.telemetry.ctx_thread", _fake_ctx_thread)
    return captured


def _carries(haystack: tuple, needle: object) -> bool:
    """实例出现在入参里就行 —— **不锁位置**：C4 会重排签名，位置本就该自由。"""
    return any(x is needle for x in haystack)


@pytest.mark.xfail(strict=True,
                   reason="C4 翻转：`/start` 无 key + 全局可用 → 200，且后台拿到全局实例")
def test_l1_start_falls_back_to_global_instance(store, user_id, monkeypatch):
    fake_global = _poison(LLMAdapter(api_key="k"))
    monkeypatch.setattr(deps, "get_llm", lambda: fake_global)
    captured = _capture_thread(monkeypatch)
    tid = _seed_text(store, user_id)

    r = TestClient(_distill_app(store, user_id)).post(
        "/api/distill/start",
        json={"text_id": tid, "character_name": "甲", "force": False},
    )

    assert r.status_code == 200, r.text
    assert captured, "没起后台线程"
    assert _carries(captured[0][1], fake_global), (
        f"后台线程拿到的不是请求时解析出的全局实例：{captured[0][1]!r}")


@pytest.mark.xfail(strict=True,
                   reason="C4 翻转：后台 llm `is` 请求时解析出的实例；签名里没有 api_config")
def test_l2_background_thread_gets_the_resolved_instance(store, user_id, monkeypatch):
    from routers import distill as D

    fake_global = _poison(LLMAdapter(api_key="k"))
    monkeypatch.setattr(deps, "get_llm", lambda: fake_global)
    captured = _capture_thread(monkeypatch)
    resolved: list[Any] = []

    real = deps.get_user_llm

    async def _spy(*a, **kw):
        llm = await real(*a, **kw)
        resolved.append(llm)
        return llm

    monkeypatch.setattr(deps, "get_user_llm", _spy)
    tid = _seed_text(store, user_id)

    r = TestClient(_distill_app(store, user_id)).post(
        "/api/distill/start",
        json={"text_id": tid, "character_name": "甲", "force": False},
    )

    assert r.status_code == 200, r.text
    assert resolved, "`/start` 没有走唯一解析出口 `get_user_llm`"
    assert captured and _carries(captured[0][1], resolved[-1]), (
        "后台线程拿到的不是请求时解析出的那个实例")
    assert "api_config" not in inspect.signature(D._run_distill_task).parameters


# ── L3：缓存热也拦得住 ────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True,
                   reason="C4 翻转：缓存命中的解析出口仍要过 ensure_llm_allowed")
def test_l3_cache_hit_still_checks_geo(store, monkeypatch):
    A = _access()
    _fake_geo(monkeypatch)
    asyncio.run(store.update_user_api_config(
        "u1", "k", "https://api.other.com", "m"))

    A.LLM_CALLER.set(A.Caller(_ALLOWED_IP, "u1"))
    first = asyncio.run(deps.get_user_llm("u1", store))       # 入缓存
    assert first is not None

    A.LLM_CALLER.set(A.Caller(_BLOCKED_IP, "u1"))             # 换 IP，缓存仍热
    with pytest.raises(A.LLMUseBlocked):
        asyncio.run(deps.get_user_llm("u1", store))


# ── L4：活会话（内存里那个陈旧实例）也要被拦 ──────────────────────────────

class _SessionEngine:
    """只做到 `/api/chat/send` 真正会碰到的那几格：一个出站、一个上下文载体。"""

    def __init__(self, llm: LLMAdapter) -> None:
        self._llm = llm
        self.history: list[dict] = []
        self.last_summary = ""
        self.last_traces: list = []
        self.user_role = ""
        self.affinity_enabled = True
        self.agent_mode = False
        self._ctx_engine = type("_Ctx", (), {"web_search_enabled": False})()
        self._storage = None
        self._user_id = ""
        self._session_id = ""
        self._main_loop = None

    def chat(self, message: str, **kw):
        return self._llm.chat("system", [{"role": "user", "content": message}])


class _AuthStore:
    """`AuthMiddleware` 只向它要两样：查用户、更新活跃时间。"""

    def __init__(self, uid: str) -> None:
        self._uid = uid

    async def get_user_by_id(self, uid: str):
        return {"id": self._uid, "username": "t", "is_admin": False}

    async def update_last_active(self, uid: str):
        return None


def _token(uid: str) -> str:
    import jwt

    from routers.auth import JWT_ALGORITHM

    return jwt.encode({"sub": uid}, TEST_JWT_SECRET, algorithm=JWT_ALGORITHM)


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：出站前挡下 → 403，且毒客户端未被触碰")
def test_l4_live_session_is_blocked_before_outbound(store, monkeypatch):
    """F6 的形状：**会话内存里那个实例**也要被拦，光拦解析出口拦不住它。

    构造上有意让**解析层放行、调用层拦**，否则这条会退化成测「解析时的提前 403」：
      - 用户配置的 `base_url` 是白名单内的（`deepseek`），且 `chat.send` 调
        `get_user_llm(user_id, storage)` **不传 client_ip**（`chat.py:609`）——
        于是解析出口这条路全程不判 geo；
      - 会话里那个 `_SessionEngine` 拿着的适配器 `base_url` 是**非白名单**的，
        且 `_client` 已换成毒对象 —— 只有调用点门能拦住它。

    **这条会牵出一处 spec 没写的地方**：`chat._do_chat` 的宽 `except Exception`
    只对 `llm_error_payload(exc) is not None` 放行，其余一律吞成 500。
    `LLMUseBlocked` 若不被放行，就永远到不了领域异常出口 —— 届时按停止条件报告，
    不当场加白名单。"""
    uid = f"u_live_{uuid.uuid4().hex[:8]}"
    _fake_geo(monkeypatch)
    asyncio.run(store.update_user_api_config(
        uid, "k", "https://api.deepseek.com", "m"))   # 白名单内：解析层必放行
    monkeypatch.setattr(server, "get_storage", lambda: _AuthStore(uid))

    llm = _poison(LLMAdapter(api_key="k", base_url="https://api.other.com"))
    sid = f"s_live_{uuid.uuid4().hex[:8]}"
    deps.get_sessions()[sid] = {
        "engine": _SessionEngine(llm), "user_id": uid,
        "lock": asyncio.Lock(), "message_ids": [],
    }
    try:
        app = FastAPI()
        app.include_router(chat_router)
        app.add_middleware(server.AuthMiddleware)
        app.dependency_overrides[get_storage] = lambda: store
        app.dependency_overrides[get_current_user] = lambda: {
            "id": uid, "username": "t", "is_admin": False,
        }
        client = TestClient(app, raise_server_exceptions=False)

        r = client.post(
            "/api/chat/send",
            json={"session_id": sid, "message": "你好"},
            headers={"Authorization": f"Bearer {_token(uid)}", "X-Real-IP": _BLOCKED_IP},
        )
    finally:
        deps.get_sessions().pop(sid, None)

    assert r.status_code == 403, r.text


# ── L5：纯策略层四情形 ────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True,
                   reason="C4 翻转：resolve_llm 四情形表 + 初始化失败的 reason 非空")
def test_l5_resolve_llm_table():
    A = _access()
    sentinel = object()

    def _ok(cfg):
        return sentinel

    def _boom(cfg):
        raise RuntimeError("bad key")

    got = A.resolve_llm({"api_key": "k"}, build_user=_ok, get_global=lambda: sentinel)
    assert got.source is A.Source.USER and got.llm is sentinel

    got = A.resolve_llm({"api_key": "k"}, build_user=_boom, get_global=lambda: sentinel)
    assert got.source is A.Source.GLOBAL and got.llm is sentinel
    assert got.reason.strip(), "初始化失败必须留下原因（D2）"

    got = A.resolve_llm({}, build_user=_ok, get_global=lambda: sentinel)
    assert got.source is A.Source.GLOBAL and got.llm is sentinel

    got = A.resolve_llm({}, build_user=_ok, get_global=lambda: None)
    assert got.source is A.Source.UNAVAILABLE and got.llm is None


# ── L6：每个出站方法都先过守卫 ────────────────────────────────────────────

#: 公开可调用成员里**不是出站**的三个：`model`/`base_url` 是只读事实，`aclose` 是收尾。
_NON_CALL = {"model", "aclose", "base_url"}


def _outbound_names() -> list[str]:
    """出站方法集从**类体现算** —— 不抄名单（新增成员自动进判据面）。"""
    return sorted(
        n for n, v in inspect.getmembers(LLMAdapter, callable)
        if not n.startswith("_") and n not in _NON_CALL
    )


def _call_args(fn) -> dict[str, Any]:
    """按签名给无默认值的形参填占位实参 —— 不求跑得通，只要走到守卫那一行。"""
    out: dict[str, Any] = {}
    for name, p in inspect.signature(fn).parameters.items():
        if name == "self" or p.default is not inspect.Parameter.empty:
            continue
        out[name] = [] if "list" in str(p.annotation) else ""
    return out


def test_l6_method_set_is_not_empty():
    """先问「X 会不会是空集」：方法集为空时，下面那组「每个都……」会恒真。"""
    assert _outbound_names()


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：注册常拦守卫后，每个出站方法都抛 LLMUseBlocked")
@pytest.mark.parametrize("name", _outbound_names())
def test_l6_every_outbound_method_hits_the_guard(name):
    A = _access()
    la = _llm_adapter_module()
    la.set_call_guard(lambda llm: (_ for _ in ()).throw(A.LLMUseBlocked("u", "blocked")))
    try:
        adapter = _poison(LLMAdapter(api_key="k"))
        fn = getattr(adapter, name)
        args = _call_args(fn)
        with pytest.raises(A.LLMUseBlocked):
            if inspect.isgeneratorfunction(fn):
                list(fn(**args))
            elif inspect.iscoroutinefunction(fn):
                asyncio.run(fn(**args))
            else:
                fn(**args)
    finally:
        la.set_call_guard(None)


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：`base_url` 是 C3 新增的只读属性；本条的命题是「_NON_CALL 不陈旧」")
def test_l6_non_call_set_matches_the_class():
    """双向校验：`_NON_CALL` 里每个名字都得真在类上 —— 陈旧条目会让
    「哪些不是出站」悄悄失真，而方法集正是靠它算的。"""
    for n in _NON_CALL:
        assert hasattr(LLMAdapter, n), f"_NON_CALL 里的 {n} 在 LLMAdapter 上不存在（名单陈旧）"


# ── L7：无上下文 fail-closed ──────────────────────────────────────────────

@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：无上下文 → LLMCallerMissing；SYSTEM 内放行")
def test_l7_missing_context_fails_closed_and_system_passes():
    A = _access()
    la = _llm_adapter_module()
    la.set_call_guard(A.ensure_llm_allowed)
    try:
        adapter = LLMAdapter(api_key="k", base_url="https://api.deepseek.com")

        with pytest.raises(A.LLMCallerMissing):
            adapter._before_call()

        with A.system_llm_context():
            adapter._before_call()      # 不抛即放行
    finally:
        la.set_call_guard(None)


# ── L8：contextvar 的可见性（每个位置 × OTEL 开/关各一条） ────────────────

def _in_thread(collect: list) -> None:
    collect.append(_caller_ip())


def _nested(collect: list) -> None:
    t = threading.Thread(target=_in_thread, args=(collect,))
    t.start()
    t.join(timeout=5)


def _in_thread_run(collect: list) -> None:
    async def _coro():
        return _caller_ip()

    collect.append(asyncio.run(_coro()))


def _spawn(kind: str) -> str | None:
    """在 *kind* 指定的派生位置读一次 contextvar，返回读到的 IP。"""
    from core import telemetry as T

    if kind in ("thread", "nested", "asyncio_run"):
        box: list = []
        entry = {"thread": _in_thread, "nested": _nested, "asyncio_run": _in_thread_run}[kind]
        t = T.ctx_thread(entry, args=(box,))
        t.start()
        t.join(timeout=5)
        return box[0] if box else None

    if kind == "submit":
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return T.ctx_submit(pool, _caller_ip).result(timeout=5)
        finally:
            pool.shutdown(wait=True)

    raise AssertionError(f"未知派生种类：{kind}")


def _via_http(streaming: bool) -> str | None:
    """走**生产中间件**，非 `/api/` 公开路径（无需 JWT）。"""
    app = FastAPI()
    app.add_middleware(server.AuthMiddleware)

    if streaming:
        @app.get("/probe")
        def probe_stream():
            def _gen():
                yield f"data: {_caller_ip()}\n\n"

            return StreamingResponse(_gen(), media_type="text/event-stream")

        r = TestClient(app).get("/probe", headers={"X-Real-IP": _BLOCKED_IP})
        line = next((ln for ln in r.text.splitlines() if ln.startswith("data: ")), "")
        return line[len("data: "):] or None

    @app.get("/probe")
    def probe():
        return {"ip": _caller_ip()}

    return TestClient(app).get("/probe", headers={"X-Real-IP": _BLOCKED_IP}).json()["ip"]


@pytest.mark.xfail(strict=True,
                   reason="C2 翻转：传播原语收敛为一个，contextvar 处处可见")
@pytest.mark.parametrize("otel", [False, True], ids=["otel-off", "otel-on"])
@pytest.mark.parametrize(
    "where", ["endpoint", "stream", "thread", "submit", "nested", "asyncio_run"])
def test_l8_contextvar_visible_everywhere(where, otel, monkeypatch):
    from core import telemetry as T

    monkeypatch.setattr(T, "_ENABLED", otel)

    if where in ("endpoint", "stream"):
        observed = _via_http(streaming=where == "stream")
    else:
        A = _access()
        A.LLM_CALLER.set(A.Caller(_BLOCKED_IP, "u1"))
        observed = _spawn(where)

    tag = f"{where}（OTEL={'on' if otel else 'off'}）"
    assert observed == _BLOCKED_IP, f"{tag} 看不到 contextvar：{observed!r}"


# ── L9：出口配码 + 审计 ───────────────────────────────────────────────────

class _AuditStore:
    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[tuple] = []
        self._boom = boom

    async def record_geo_block(self, user_id, ip, base_url, reason):
        self.calls.append((user_id, ip, base_url, reason))
        if self._boom:
            raise RuntimeError("audit db down")
        return 1


def _audit_app(uid: str) -> FastAPI:
    from starlette.middleware.base import BaseHTTPMiddleware

    A = _access()
    app = FastAPI()
    server.register_domain_error_handlers(app)

    class _Who(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = {"id": uid}
            return await call_next(request)

    app.add_middleware(_Who)

    @app.get("/boom")
    def boom():
        raise A.LLMUseBlocked("https://api.other.com", "境内不支持境外模型")

    return app


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：LLMUseBlocked → 403，且审计恰好一次")
def test_l9_blocked_maps_to_403_and_audits_once(monkeypatch):
    store = _AuditStore()
    monkeypatch.setattr(server, "get_storage", lambda: store)
    r = TestClient(_audit_app("u9"), raise_server_exceptions=False).get(
        "/boom", headers={"X-Real-IP": _BLOCKED_IP})

    assert r.status_code == 403, r.text
    assert len(store.calls) == 1, f"审计次数不对：{store.calls}"
    assert r.json()["detail"]


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：审计抛错仍返回 403（审计失败不改变判定）")
def test_l9_audit_failure_keeps_the_403(monkeypatch):
    store = _AuditStore(boom=True)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    r = TestClient(_audit_app("u9"), raise_server_exceptions=False).get(
        "/boom", headers={"X-Real-IP": _BLOCKED_IP})

    assert r.status_code == 403, r.text


# ── L10：构造点结构锁（②层） ──────────────────────────────────────────────

def _web_py() -> list[Any]:
    """`web/` 下待扫的 .py —— `web/app.py` 不在扫描面（已标 deprecated，
    留着只会让这条锁恒红，而它守的「生产构造点唯一」与那个死文件无关）。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    return [p for p in sorted((root / "web").rglob("*.py")) if p.name != "app.py"]


def _bare_ok_lines() -> set[int]:
    """`web/deps.py` 里允许无参构造的行号 —— 由 `get_llm` / `reset_llm_and_dependents`
    **现算**，不手抄：搬了函数体、加了行，这里自己跟上。"""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "web" / "deps.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    ok: set[int] = set()
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name not in ("get_llm", "reset_llm_and_dependents"):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name == "LLMAdapter":
                ok.add(node.lineno)
    return ok


_BARE_OK_LINES = _bare_ok_lines()


def _web_llm_adapter_calls() -> list[tuple[str, int, bool]]:
    """`web/` 下所有 `LLMAdapter(...)` → (相对路径, 行号, 是否带参数)。"""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    out: list[tuple[str, int, bool]] = []
    for path in _web_py():
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name == "LLMAdapter":
                out.append((rel, node.lineno, bool(node.args) or bool(node.keywords)))
    return out


def test_l10_scan_face_is_not_empty():
    """空集会让「所有命中都合规」恒真。"""
    assert _web_llm_adapter_calls(), "web/ 下一处 LLMAdapter(...) 都没扫到 —— 扫描面坏了"


@pytest.mark.xfail(strict=True,
                   reason="C5 翻转：web/ 下带参构造只留唯一出口，无参只留 get_llm/reset")
def test_l10_construction_points_are_confined():
    """带参数的 `LLMAdapter(...)` 只允许出现在 `web/deps.py`（USER 构造工厂）；
    无参调用只允许出现在 `get_llm` / `reset_llm_and_dependents`。

    **已知冲突**：`web/routers/market.py:471` 有一处无参 `LLMAdapter()`，spec §5 把它
    列为「只报告不修」—— 那样这条锁到 C5 仍是红的。两条路：把 market 那处改走
    `get_llm()`（既过锁，又让 auto_review 也受本门约束），或给无参子句一条明示豁免
    （明示豁免 = 第二份手工清单，§四不取）。**需要裁定，不自行处置。**

    **已知盲区**：别名绕过（`A = LLMAdapter; A(api_key=...)`）。AST 认不出别名，
    这条锁看不见 —— 写在这里，不假装它挡住了。
    """
    offenders = []
    for rel, ln, has_args in _web_llm_adapter_calls():
        if rel != "web/deps.py":
            offenders.append((rel, ln, "带参" if has_args else "无参"))
        elif not has_args and ln not in _BARE_OK_LINES:
            offenders.append((rel, ln, "无参"))
    assert not offenders, (
        "web/ 下只允许 deps.py 这一个构造出口（带参 = USER 工厂，"
        f"无参 = get_llm/reset_llm_and_dependents）。越界：{offenders}")


# ── L11：装配后守卫就是策略层那一个函数 ───────────────────────────────────

@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：导入生产 app 后 adapter 的守卫 is ensure_llm_allowed")
def test_l11_app_installs_the_production_guard():
    A = _access()
    la = _llm_adapter_module()
    getter = getattr(la, "get_call_guard", None)
    assert getter is not None, "adapters.llm_adapter 没发布与 set_call_guard 配对的读取口"
    server  # noqa: B018 —— 导入生产装配层；注册发生在 import 期
    assert getter() is A.ensure_llm_allowed
