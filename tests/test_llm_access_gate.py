# -*- coding: utf-8 -*-
"""锁：LLM 访问门收敛 + core 反向依赖根除 —— spec v5 §4 的 L1–L15。

spec 在库：`docs/specs/llm-access-gate.md`（v5 全量取代 v1–v4）。命题与归属见那张表，
此处不重抄，只写**本文件自己的边界**。

**除少数「非空守卫」外全部 `xfail(strict=True)`。** 这是 red-first 的形态：判据先落，
本步（C1′）只写锁、不改生产代码，故这些用例现在**必须都是红的**。strict 让「红→绿」
有声响 —— 某一步把某条做绿了，它立刻以 **XPASS** 报错，逼人把标记摘掉；标记没摘就
说明这条还没真绿，不会被静默当成通过（§四「豁免即静默放行」的反面）。

**非空守卫为什么现在必须绿**：命题形如「所有命中都合规」，命中集为空时恒真。
故先把「命中集非空」单列成一条**当下就绿**的用例 —— 它红了说明扫描面坏了，
而不是被判对象合规。

**边界（写清楚免得被当成漏洞）**：
  - 不测真 LLM 网络。凡涉出站的用例都把适配器的客户端换成**毒对象**（一碰即失败），
    证的是「请求在出站前就被挡下」，不是「挡下之后恰好也没发出去」。
  - L8 的端点/流式两条走**带身份的 `/api/` 路径**（需 JWT），公开路径那条走非 `/api/`
    路径（免 JWT）—— 三者都必须拿到 contextvar，这正是「中间件单出口」要证的。
  - L10 的扫描面不含 `web/app.py`：Gradio 死代码，docstring 标 `.. deprecated::`，
    `Dockerfile` / `docker-compose*.yml` / `start_all.bat` / `.github/workflows/` 零引用
    （AGENTS.md 已记）。把它算进来只会让锁恒红，且它守的与那个死文件无关。
  - 别名绕过（`A = LLMAdapter; A(...)`）AST 认不出 —— L10 / L12 / L13 都看不见，
    写在这里，不假装它们挡住了。
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
# C2/C3/C4 才建 `web/llm_gate.py` / `web/llm_resolution.py` / `core/scheduling.py`
# / `core/concurrency.py`；模块级 import 会让整个文件**收集期报错**（十几条一起消失，
# 看上去像「没写过」），而不是一批可读的红。故走函数内导入。

def _mod(name: str):
    import importlib

    return importlib.import_module(name)


def _gate():
    """`web/llm_gate.py`：上下文、守卫、审计。"""
    return _mod("web.llm_gate")


def _resolution():
    """`web/llm_resolution.py`：解析策略（纯函数）。"""
    return _mod("web.llm_resolution")


def _adapter():
    return _mod("adapters.llm_adapter")


def _caller_ip() -> str | None:
    """当前上下文里的调用方 IP —— 读不到上下文返回 None（不伪造默认值）。"""
    caller = _gate().LLM_CALLER.get(None)
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

    两个绑定点都打 —— 策略层既可能 `import web.geo_guard as G` 再 `G.check_api_allowed`，
    也可能 `from web.geo_guard import check_api_allowed`；锁不该锁定那一步选哪种写法。
    """
    import web.geo_guard as G

    def verdict(ip, url):
        if ip != blocked or "deepseek" in (url or ""):
            return True, ""
        return False, "境内不支持境外模型"

    monkeypatch.setattr(G, "check_api_allowed", verdict)
    try:
        gate = _gate()
    except ImportError:
        return
    if hasattr(gate, "check_api_allowed"):
        monkeypatch.setattr(gate, "check_api_allowed", verdict)


class _SetGuard:
    """临时换掉调用点守卫，退出时按**原值**还原（不是置 None —— 生产装配过）。"""

    def __init__(self, fn) -> None:
        self._la = _adapter()
        self._fn = fn

    def __enter__(self):
        self._prev = self._la.get_call_guard()
        self._la.set_call_guard(self._fn)
        return self

    def __exit__(self, *exc):
        self._la.set_call_guard(self._prev)
        return False


def _patch_store(monkeypatch, store) -> None:
    """把 storage 单例换成假的 —— `deps` 与 `web.llm_gate` 两处绑定都打。"""
    monkeypatch.setattr(deps, "get_storage", lambda: store)
    try:
        gate = _gate()
    except ImportError:
        return
    if hasattr(gate, "get_storage"):
        monkeypatch.setattr(gate, "get_storage", lambda: store)


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
    """把派生原语换成「只记参数、不起线程」—— `/start` 的入参由此取得。

    迁移（§2.3）把 `ctx_thread` 从 telemetry 搬到 concurrency，故两个模块都打；
    **要求调用点用属性形式**（`C.ctx_thread(...)` 而非 `from ... import ctx_thread`），
    否则函数内绑定在 import 期就固化了，这里拦不到。
    """
    captured: list[tuple] = []

    def _fake_ctx_thread(target, args=(), kwargs=None, **kw):
        captured.append((target, args, kwargs or {}))
        return threading.Thread()

    for mod_name in ("core.concurrency", "core.telemetry"):
        try:
            mod = _mod(mod_name)
        except ImportError:
            continue
        if hasattr(mod, "ctx_thread"):
            monkeypatch.setattr(mod, "ctx_thread", _fake_ctx_thread)
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
                   reason="C4 翻转：后台 llm `is` 请求时解析出的实例；签名换成 llm/embedding_*")
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

    params = inspect.signature(D._run_distill_task).parameters
    assert "api_config" not in params, "签名里还留着 api_config —— 线程内仍在自行解析"
    for required in ("llm", "embedding_key", "embedding_region"):
        assert required in params, f"签名缺 {required}（§2.10）"


# ── L3：缓存热也拦得住 ────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True,
                   reason="C4 翻转：缓存命中也过 preflight，判据 kind == call_refused")
def test_l3_cache_hit_still_checks_geo(store, monkeypatch):
    gate = _gate()
    la = _adapter()
    _fake_geo(monkeypatch)
    uid = f"u_l3_{uuid.uuid4().hex[:8]}"
    asyncio.run(store.update_user_api_config(
        uid, "k", "https://api.other.com", "m"))

    gate.LLM_CALLER.set(gate.Caller(_ALLOWED_IP, uid))
    first = asyncio.run(deps.get_user_llm(uid, store))       # 入缓存
    assert first is not None

    gate.LLM_CALLER.set(gate.Caller(_BLOCKED_IP, uid))       # 换 IP，缓存仍热
    with pytest.raises(la.LLMCallRefused) as ei:
        asyncio.run(deps.get_user_llm(uid, store))

    payload = la.llm_error_payload(ei.value) or {}
    assert payload.get("kind") == "call_refused", f"判别键不是 call_refused：{payload}"


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
                   reason="C3 翻转：出站前挡下 → 403（call_refused 配码），且毒客户端未被触碰")
def test_l4_live_session_is_blocked_before_outbound(store, monkeypatch):
    """F6 的形状：**会话内存里那个实例**也要被拦，光拦解析出口拦不住它。

    构造上有意让**解析层放行、调用层拦**，否则这条会退化成测「解析时的提前 403」：
      - 用户配置的 `base_url` 是白名单内的（`deepseek`），且 `chat.send` 的解析出口
        （`get_user_llm`）**不再收 client_ip**（§2.8）—— 于是解析这条路全程不判 geo；
      - 会话里那个 `_SessionEngine` 拿着的适配器 `base_url` 是**非白名单**的，
        且客户端已换成毒对象 —— 只有调用点门能拦住它。

    应用要按 §2.6 装配（`install_llm_gate` + 领域异常出口），否则 `LLMCallRefused`
    会被 `_do_chat` 之外的路径吞成 500 而不是 403。"""
    gate = _gate()
    uid = f"u_live_{uuid.uuid4().hex[:8]}"
    _fake_geo(monkeypatch)
    asyncio.run(store.update_user_api_config(
        uid, "k", "https://api.deepseek.com", "m"))   # 白名单内：解析层必放行
    _patch_store(monkeypatch, _AuthStore(uid))
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
        server.register_domain_error_handlers(app)
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
    R = _resolution()
    sentinel = object()

    def _ok(cfg):
        return sentinel

    def _boom(cfg):
        raise RuntimeError("bad key")

    got = R.resolve_llm({"api_key": "k"}, build_user=_ok, get_global=lambda: sentinel)
    assert got.source is R.Source.USER and got.llm is sentinel

    got = R.resolve_llm({"api_key": "k"}, build_user=_boom, get_global=lambda: sentinel)
    assert got.source is R.Source.GLOBAL and got.llm is sentinel
    assert got.reason.strip(), "初始化失败必须留下原因（D2）"

    got = R.resolve_llm({}, build_user=_ok, get_global=lambda: sentinel)
    assert got.source is R.Source.GLOBAL and got.llm is sentinel

    got = R.resolve_llm({}, build_user=_ok, get_global=lambda: None)
    assert got.source is R.Source.UNAVAILABLE and got.llm is None


# ── L6：每个出站方法都先过守卫 ────────────────────────────────────────────

#: 公开可调用成员里**不是出站**的三个：`model`/`base_url` 是只读事实，`aclose` 是收尾。
_NON_CALL = {"model", "aclose", "base_url"}

_REFUSAL = "总拒绝：本用例注册的守卫"


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
    """非空守卫：方法集为空时，下面那组「每个都……」会恒真。"""
    assert _outbound_names()


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：注册常拦守卫后，每个出站方法都抛 LLMCallRefused")
@pytest.mark.parametrize("name", _outbound_names())
def test_l6_every_outbound_method_hits_the_guard(name):
    la = _adapter()
    with _SetGuard(lambda base_url: _REFUSAL):
        adapter = _poison(LLMAdapter(api_key="k"))
        fn = getattr(adapter, name)
        args = _call_args(fn)
        with pytest.raises(la.LLMCallRefused) as ei:
            if inspect.isgeneratorfunction(fn):
                list(fn(**args))
            elif inspect.iscoroutinefunction(fn):
                asyncio.run(fn(**args))
            else:
                fn(**args)
        assert _REFUSAL in str(ei.value), "拒绝异常没带上守卫给的理由"


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：`base_url` 是 C3 新增的只读属性；本条的命题是「_NON_CALL 不陈旧」")
def test_l6_non_call_set_matches_the_class():
    """反向校验：`_NON_CALL` 里每个名字都得真在类上 —— 陈旧条目会让「哪些不是出站」
    悄悄失真，而方法集正是靠它算的。另一向（非空集、逐个被拦）由上面两条钉住。"""
    for n in _NON_CALL:
        assert hasattr(LLMAdapter, n), f"_NON_CALL 里的 {n} 在 LLMAdapter 上不存在（名单陈旧）"


# ── L7：无上下文 fail-closed ──────────────────────────────────────────────

@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：无上下文 → LLMCallerMissing；SYSTEM 内放行")
def test_l7_missing_context_fails_closed_and_system_passes():
    gate = _gate()

    with pytest.raises(gate.LLMCallerMissing):
        gate.geo_call_guard("https://api.deepseek.com")

    with gate.system_llm_context():
        assert gate.geo_call_guard("https://api.deepseek.com") is None

    # 同一件事在**出站方法**那一侧也要成立 —— 守卫是注入进适配器的。
    with _SetGuard(gate.geo_call_guard):
        adapter = LLMAdapter(api_key="k", base_url="https://api.deepseek.com")
        with pytest.raises(gate.LLMCallerMissing):
            adapter._before_call()
        with gate.system_llm_context():
            adapter._before_call()      # 不抛即放行


# ── L8：contextvar 的可见性（每个位置 × OTEL 开/关各一条） ────────────────

def _in_thread(collect: list) -> None:
    collect.append(_caller_ip())


def _nested(collect: list) -> None:
    """嵌套派生：`ctx_thread` 里再 `ctx_thread` —— 传播是逐层拷贝的。"""
    C = _mod("core.concurrency")
    t = C.ctx_thread(_in_thread, args=(collect,))
    t.start()
    t.join(timeout=5)


def _in_thread_run(collect: list) -> None:
    """线程内 `asyncio.run` —— 拷贝出的 context 要跟着进入新 loop。"""

    async def _coro():
        return _caller_ip()

    collect.append(asyncio.run(_coro()))


def _spawn(kind: str) -> str | None:
    """在 *kind* 指定的派生位置读一次 contextvar，返回读到的 IP。"""
    C = _mod("core.concurrency")

    if kind in ("thread", "nested", "asyncio_run"):
        box: list = []
        entry = {"thread": _in_thread, "nested": _nested, "asyncio_run": _in_thread_run}[kind]
        t = C.ctx_thread(entry, args=(box,))
        t.start()
        t.join(timeout=5)
        return box[0] if box else None

    if kind == "submit":
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return C.ctx_submit(pool, _caller_ip).result(timeout=5)
        finally:
            pool.shutdown(wait=True)

    raise AssertionError(f"未知派生种类：{kind}")


def _http_app() -> FastAPI:
    """走**生产中间件**的最小 app：带身份 `/api/` 路径 + 公开（非 `/api/`）路径。"""
    app = FastAPI()
    app.add_middleware(server.AuthMiddleware)
    uid = "u_l8"

    @app.get("/probe")
    def probe_public():
        return {"ip": _caller_ip()}

    @app.get("/api/probe")
    def probe_api():
        return {"ip": _caller_ip()}

    @app.get("/api/probe_stream")
    def probe_stream():
        def _gen():
            yield f"data: {_caller_ip()}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    return app


def _via_http(kind: str) -> str | None:
    """三个 HTTP 位置各读一次 contextvar。

    `endpoint` = `/api/` 上的普通响应；`stream` = `/api/` 上的流式响应体；
    `public` = 非 `/api/` 路径（免 JWT）。前两者带 token 走鉴权分支，
    后者走公开分支 —— §2.9 要求**两条分支都**设值，且在 `call_next` 之前。
    """
    uid = "u_l8"
    app = _http_app()
    client = TestClient(app)
    headers = {"X-Real-IP": _BLOCKED_IP}
    if kind != "public":
        headers["Authorization"] = f"Bearer {_token(uid)}"

    if kind == "stream":
        r = client.get("/api/probe_stream", headers=headers)
        line = next((ln for ln in r.text.splitlines() if ln.startswith("data: ")), "")
        return line[len("data: "):] or None

    path = "/probe" if kind == "public" else "/api/probe"
    return client.get(path, headers=headers).json()["ip"]


@pytest.mark.xfail(strict=True,
                   reason="C2b 翻转：传播原语收敛为一个，contextvar 处处可见")
@pytest.mark.parametrize("otel", [False, True], ids=["otel-off", "otel-on"])
@pytest.mark.parametrize(
    "where", ["endpoint", "stream", "public", "thread", "submit", "nested", "asyncio_run"])
def test_l8_contextvar_visible_everywhere(where, otel, monkeypatch):
    from core import telemetry as T

    monkeypatch.setattr(T, "_ENABLED", otel)

    if where in ("endpoint", "stream", "public"):
        monkeypatch.setattr(server, "get_storage", lambda: _AuthStore("u_l8"))
        observed = _via_http(where)
    else:
        gate = _gate()
        gate.LLM_CALLER.set(gate.Caller(_BLOCKED_IP, "u_l8"))
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
            raise RuntimeError("审计库挂了")
        return 1


class _Submitter:
    """录下投递、并按需把协程跑掉 —— 并发的真实语义不重要，**投递这件事**才是判据。"""

    def __init__(self, *, run: bool = True) -> None:
        self.calls: list[tuple] = []
        self._run = run

    def __call__(self, coro, *, wait: bool = True, timeout: float | None = None):
        self.calls.append((coro, wait))
        if not self._run:
            coro.close()
            return None
        return asyncio.run(coro)


def _audit_app(uid: str) -> FastAPI:
    from starlette.middleware.base import BaseHTTPMiddleware

    gate = _gate()
    app = FastAPI()
    server.register_domain_error_handlers(app)
    gate.install_llm_gate(app)

    class _Who(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = {"id": uid}
            gate.LLM_CALLER.set(gate.Caller(request.headers.get("X-Real-IP", ""), uid))
            return await call_next(request)

    app.add_middleware(_Who)

    @app.get("/boom")
    def boom():
        raise _adapter().LLMCallRefused("境内不支持境外模型", "https://api.other.com")

    return app


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：拒绝 → 403，且文案就是拒绝理由")
def test_l9_blocked_maps_to_403_with_the_reason(monkeypatch):
    store = _AuditStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    sched = _mod("core.scheduling")
    sub = _Submitter()
    sched.set_loop_submitter(sub)
    try:
        r = TestClient(_audit_app("u9"), raise_server_exceptions=False).get(
            "/boom", headers={"X-Real-IP": _BLOCKED_IP})
    finally:
        sched.set_loop_submitter(None)

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "境内不支持境外模型", r.text
    assert len(sub.calls) == 1, f"拒绝没有经投递原语发出去：{sub.calls}"
    assert sub.calls[0][1] is False, "审计投递必须是 wait=False（不阻塞请求）"


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：审计投递恰好一次，user 与 ip 取自 Caller")
def test_l9_audit_is_delivered_once_with_the_caller(monkeypatch):
    store = _AuditStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    sched = _mod("core.scheduling")
    sub = _Submitter()
    sched.set_loop_submitter(sub)
    try:
        r = TestClient(_audit_app("u9"), raise_server_exceptions=False).get(
            "/boom", headers={"X-Real-IP": _BLOCKED_IP})
    finally:
        sched.set_loop_submitter(None)

    assert r.status_code == 403, r.text
    assert len(store.calls) == 1, f"审计次数不对：{store.calls}"
    uid, ip, base_url, reason = store.calls[0]
    assert uid == "u9" and ip == _BLOCKED_IP, f"审计的身份不是 Caller 里的：{store.calls[0]}"
    assert base_url == "https://api.other.com" and reason.strip()


@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：审计抛错仍返回 403（审计失败不改变判定），且不阻塞请求")
def test_l9_audit_failure_keeps_the_403(monkeypatch):
    store = _AuditStore(boom=True)
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    sched = _mod("core.scheduling")
    sub = _Submitter()
    sched.set_loop_submitter(sub)
    try:
        r = TestClient(_audit_app("u9"), raise_server_exceptions=False).get(
            "/boom", headers={"X-Real-IP": _BLOCKED_IP})
    finally:
        sched.set_loop_submitter(None)

    assert r.status_code == 403, r.text
    assert len(sub.calls) == 1, "判定改了 —— 审计失败不该拦下请求"


# ── L10：构造点结构锁（②层） ──────────────────────────────────────────────

def _web_py() -> list[Any]:
    """`web/` 下待扫的 .py —— `web/app.py` 不在扫描面（Gradio 死代码，AGENTS.md 已记，
    `Dockerfile` / compose / workflow 零引用）：留着只会让这条锁恒红，而它守的
    「生产构造点唯一」与那个死文件无关。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    return [p for p in sorted((root / "web").rglob("*.py")) if p.name != "app.py"]


def _owners(tree):
    """行号 → 所属 "Class.method" / "func" / "<module>"（最内层）。"""
    import ast

    out: dict[int, str] = {}

    def walk(node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}{child.name}"
                for ln in range(child.lineno, (getattr(child, "end_lineno", child.lineno)) + 1):
                    out[ln] = qname
                walk(child, f"{qname}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def _calls_named(name: str, *, dirs: tuple[str, ...] = ()) -> list[tuple[str, int, str]]:
    """全仓（或指定目录）里 `name(...)` 的调用点 → (相对路径, 行号, 所属函数)。

    扫描面 = `git ls-files '*.py'` 去掉 `tests/` 与 `scripts/`（只认生产入库文件，
    干净克隆与本地跑出的数一致）。**只认调用**，不认定义 —— storage 里那几个
    同名方法定义因此天然不计入。
    """
    import ast
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8").stdout
    files = [p for p in out.splitlines()
             if p and not p.startswith("tests/") and not p.startswith("scripts/")]
    if dirs:
        files = [p for p in files if p.split("/")[0] in dirs]

    hits: list[tuple[str, int, str]] = []
    for rel in files:
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        owners = _owners(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            leaf = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if leaf == name:
                hits.append((rel, node.lineno, owners.get(node.lineno, "<module>")))
    return sorted(hits)


def _adapter_sites() -> list[tuple[str, int, str]]:
    """`web/` 下所有 `LLMAdapter(...)` → (相对路径, 行号, 所属函数)。"""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    out: list[tuple[str, int, str]] = []
    for path in _web_py():
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        owners = _owners(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            leaf = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if leaf == "LLMAdapter":
                out.append((rel, node.lineno, owners.get(node.lineno, "<module>")))
    return out


def test_l10_scan_face_is_not_empty():
    """非空守卫：空集会让「所有命中都合规」恒真。"""
    assert _adapter_sites(), "web/ 下一处 LLMAdapter(...) 都没扫到 —— 扫描面坏了"


@pytest.mark.xfail(strict=True,
                   reason="C5 翻转：构造点收敛到 `_make_user_llm` / `_make_global_llm` 两处")
def test_l10_construction_points_are_confined():
    """`web/` 下 `LLMAdapter(` **恰为 2 处**，且分别落在 `_make_user_llm`
    与 `_make_global_llm` 内（§2.8：仅有的两个构造工厂，先例 `_make_indexing_service`）。

    **已知盲区**：别名绕过（`A = LLMAdapter; A(api_key=...)`）—— AST 认不出，
    这条锁看不见，写在这里，不假装它挡住了。
    """
    sites = _adapter_sites()
    where = [(rel, owner) for rel, _, owner in sites]
    allowed = {("web/deps.py", "_make_user_llm"), ("web/deps.py", "_make_global_llm")}
    assert len(sites) == 2, (
        f"web/ 下 LLMAdapter( 应为 2 处（两个构造工厂），实际 {len(sites)} 处："
        f"{[(rel, ln, o) for rel, ln, o in sites]}")
    assert set(where) == allowed, (
        f"两处构造点的归属不是那两个工厂：{where}")


# ── L11：装配后守卫就是策略层那一个函数 ───────────────────────────────────

@pytest.mark.xfail(strict=True,
                   reason="C3 翻转：导入生产 app 后 adapter 的守卫 is geo_call_guard")
def test_l11_app_installs_the_production_guard():
    gate = _gate()
    la = _adapter()
    getter = getattr(la, "get_call_guard", None)
    assert getter is not None, "adapters.llm_adapter 没发布与 set_call_guard 配对的读取口"
    server  # noqa: B018 —— 导入生产装配层；注册发生在 import 期
    assert getter() is gate.geo_call_guard


# ── L12：策略单点（②层） ──────────────────────────────────────────────────

def test_l12_scan_face_is_not_empty():
    """非空守卫：下面两条断言「恰为 1 处」，但若扫描面坏成空，报告会误导成
    「策略已收敛」。先证明扫描面看得见东西。"""
    assert _calls_named("check_api_allowed"), "生产代码里一处 check_api_allowed 都没扫到"


@pytest.mark.xfail(strict=True,
                   reason="C5 翻转：策略单点收敛到 geo_refusal / emit_geo_block_audit")
def test_l12_policy_calls_are_single_and_contained():
    """geo 策略只有一个调用点，且落在指定的那个函数里（§2.6）。

    「带非空守卫」= 命中的**必须真在**那个容器函数内 —— 容器被改名/搬家、
    或调用点逸出到别处，都红。
    """
    checks = _calls_named("check_api_allowed")
    audits = _calls_named("record_geo_block")

    assert len(checks) == 1, (
        f"check_api_allowed( 在生产应为 1 处（geo_refusal 内），实际 {len(checks)}：{checks}")
    assert checks[0][2] == "geo_refusal", (
        f"那唯一一处不在 geo_refusal 内，而在 {checks[0][2]}：{checks[0]}")

    assert len(audits) == 1, (
        f"record_geo_block( 在生产应为 1 处（emit_geo_block_audit 内），实际 {len(audits)}：{audits}")
    assert audits[0][2] == "emit_geo_block_audit", (
        f"那唯一一处不在 emit_geo_block_audit 内，而在 {audits[0][2]}：{audits[0]}")


# ── L13：core / adapters 不 import web（反向依赖根除） ────────────────────

_FORBIDDEN_TOP = ("deps", "web", "routers")


def _scan_prod_imports():
    """[(rel, lineno, 源码行, 原文)] for 每个指向 web 层的 import。

    扫描面 = `git ls-files '*.py'` 里的 `core/` 与 `adapters/`（干净克隆与本地一致）。
    **模块级与函数体内的 import 都算** —— 当前这 10 处正是函数体内的局部 import。
    """
    import ast
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8").stdout
    files = [p for p in out.splitlines()
             if p.startswith(("core/", "adapters/"))]

    for rel in files:
        src = (root / rel).read_text(encoding="utf-8")
        lines = src.splitlines()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            top = ""
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in _FORBIDDEN_TOP:
                        yield rel, node.lineno, lines[node.lineno - 1].strip(), src
            if top in _FORBIDDEN_TOP:
                yield rel, node.lineno, lines[node.lineno - 1].strip(), src


@pytest.mark.xfail(strict=True,
                   reason="C2a 翻转：core/adapters 的反向依赖改注入后，扫描面归零")
def test_l13_core_and_adapters_do_not_import_web():
    """core/ 与 adapters/ 里不得出现指向 web 层（deps/web/routers）的 import。

    import 语句本身就是事实，不需要推断 —— 这是「唯一允许向下依赖」那条不变量
    的可判定形式。函数体内的局部 import 也算：那正是现在这 10 处的形态。
    """
    hits = [x for x in _scan_prod_imports()]
    assert not hits, (
        f"core/adapters 里还有 {len(hits)} 处指向 web 层的 import：\n" +
        "\n".join(f"  {rel}:{ln}  {text}" for rel, ln, text, _ in hits))


# ── L14：跨 loop 投递的唯一原语 ───────────────────────────────────────────

@pytest.mark.xfail(strict=True,
                   reason="C2a 翻转：core/scheduling.py 的投递原语落地")
def test_l14_submit_delegates_when_registered():
    S = _mod("core.scheduling")
    seen: list[tuple] = []

    def _recorder(coro, *, wait: bool = True, timeout: float | None = None):
        seen.append((coro, wait))
        coro.close()
        return "DELEGATED"

    async def _noop():
        return "MINE"

    S.set_loop_submitter(_recorder)
    try:
        got = S.submit_to_main_loop(_noop(), wait=False)
    finally:
        S.set_loop_submitter(None)

    assert got == "DELEGATED", f"已注册时没有委托给注册实现（拿到 {got!r}）"
    assert seen and seen[0][1] is False, f"wait 没透传：{seen}"


@pytest.mark.xfail(strict=True,
                   reason="C2a 翻转：未注册时的回退语义（wait=True 阻塞 / wait=False 不阻塞）")
def test_l14_unregistered_fallback_semantics():
    S = _mod("core.scheduling")

    async def _value():
        return "RAN"

    S.set_loop_submitter(None)
    # wait=True：阻塞取结果，且响亮（发 warning）
    with pytest.warns(Warning):
        assert S.submit_to_main_loop(_value()) == "RAN"

    # wait=False：当前线程有运行中的 loop → 不阻塞，但要真的跑起来
    ran: list[str] = []

    async def _flag():
        ran.append("RAN")

    async def _driver():
        S.submit_to_main_loop(_flag(), wait=False)

    asyncio.run(_driver())
    assert ran == ["RAN"], "wait=False 在无注册实现时把协程丢了（既没 create_task 也没 asyncio.run）"


@pytest.mark.xfail(strict=True,
                   reason="C2a 翻转：try_record_usage 的自建 loop 线程改为经原语投递")
def test_l14_usage_recording_goes_through_the_primitive():
    """`core/utils.py` 的记账出口不许再自建 event loop —— 投递这件事只在一个地方定义。"""
    S = _mod("core.scheduling")
    seen: list[tuple] = []

    def _recorder(coro, *, wait: bool = True, timeout: float | None = None):
        seen.append((coro, wait))
        coro.close()
        return None

    class _Storage:
        async def record_usage(self, *a, **kw):
            return None

    class _LLM:
        last_usage = {"prompt_tokens": 1, "completion_tokens": 2}
        _model = "m"

    S.set_loop_submitter(_recorder)
    try:
        _mod("core.utils").try_record_usage(_Storage(), "u14", _LLM(), action="chat")
    finally:
        S.set_loop_submitter(None)

    assert seen, "记账没有经投递原语出去（还在自建 loop）"
    assert seen[0][1] is False, f"记账投递必须是 wait=False：{seen}"


# ── L15：传播模块不碰 opentelemetry；载体协议被派生路径驱动 ───────────────

_OTEL_MODULES = ("opentelemetry",)


@pytest.mark.xfail(strict=True,
                   reason="C2b 翻转：core/concurrency.py 建立后，扫描面才可能非空")
def test_l15_concurrency_scan_face_is_not_empty():
    """非空守卫：文件不在/扫不到时，「不含 opentelemetry」会假绿（扫了个空气）。

    它本步就红 —— 与被它守的那条同一步翻转：red-first 里「守卫也红」不矛盾，
    它红的原因就是被守对象还没建立，正是它要证明的事。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    concurrency = root / "core" / "concurrency.py"
    assert concurrency.is_file(), "core/concurrency.py 还不存在 —— 扫描面为空"
    assert concurrency.read_text(encoding="utf-8").strip(), "core/concurrency.py 是空文件"


@pytest.mark.xfail(strict=True,
                   reason="C2b 翻转：core/concurrency.py 建立，且不 import opentelemetry")
def test_l15_concurrency_has_no_opentelemetry():
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "core" / "concurrency.py").read_text(encoding="utf-8")
    found = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _OTEL_MODULES:
                found.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in _OTEL_MODULES:
                    found.append((node.lineno, a.name))
    assert not found, f"core/concurrency.py 里出现了 opentelemetry：{found}"


@pytest.mark.xfail(strict=True,
                   reason="C2b 翻转：载体协议（capture/restore/release）被派生路径按序驱动")
def test_l15_carrier_protocol_is_driven_by_the_derive_path():
    """载体协议被驱动 = 一次 `ctx_thread` 里 capture → restore → release 按序发生。

    **已知缺口**：「OTEL 开启时 telemetry 已注册载体」这一半 spec 没给观测点
    （载体注册没有与 `set_call_guard` / `get_call_guard` 配对的读口），故本锁只钉
    可由事实判定的部分 —— 协议本身被派生路径驱动。缺口写在 spec §1.1，不假装它被覆盖。
    """
    C = _mod("core.concurrency")
    log: list[str] = []

    class _Carrier:
        def capture(self):
            log.append("capture")
            return {"v": 1}

        def restore(self, state):
            log.append("restore")
            return "token"

        def release(self, token):
            log.append("release")

    C.register_context_carrier(_Carrier())
    box: list = []
    t = C.ctx_thread(box.append, args=("ran",))
    t.start()
    t.join(timeout=5)

    assert box == ["ran"], "派生体没跑"
    assert log[:1] == ["capture"], f"调用方线程没先 capture：{log}"
    assert "restore" in log and "release" in log, f"子线程没按序 restore/release：{log}"
    assert log.index("restore") < log.index("release"), f"顺序反了：{log}"
