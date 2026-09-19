# -*- coding: utf-8 -*-
"""锁：LLM 访问门收敛 + core 反向依赖根除 —— spec v5 §4 的 L1–L15。

spec 在库：`docs/specs/llm-access-gate.md`（v5 全量取代 v1–v4）。命题与归属见那张表，
此处不重抄，只写**本文件自己的边界**。

**判据先落，未翻转的用例一律 `xfail(strict=True)` 而必须红。** 这是 red-first 的形态：
每条锁在它所属的那一步（见 spec §3 的表）由红转绿，同时摘掉标记。strict 让「红→绿」
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
import contextvars
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
# 尚未落地的那几个（`web/llm_gate.py` / `web/llm_resolution.py` / `core/concurrency.py`）
# 要到各自那一步才建；模块级 import 会让整个文件**收集期报错**（十几条一起消失，看上去
# 像「没写过」），而不是一批可读的红。故一律走函数内导入 —— 已落地的也照旧，免得后加的
# 新符号又把收集期打挂。（`core/scheduling.py` 是 C2a 落地的，见 spec §1.1。）

def _mod(name: str):
    import importlib

    return importlib.import_module(name)


def _gate():
    """`web/llm_gate.py`：守卫、审计（C3）。身份上下文**不在这里** —— 见 `_ctx()`。"""
    return _mod("web.llm_gate")


def _ctx():
    """`web/request_context.py`：请求身份上下文（C2b 建，门在 C3 import 它）。

    与 `_gate()` 分开是职责线，不是搬家：`Caller` / `LLM_CALLER` / `system_llm_context`
    是「谁在调」这个**事实**；`web/llm_gate.py` 的 geo 判定是「许不许调」这个**策略**。
    spec §2.6 要求门**依赖**本模块、不重复定义 —— 于是本文件读身份一律走这里，
    读策略一律走 `_gate()`，两个模块的身份对象必须同一个（`web/` 无 `__init__.py`，
    换个模块名会 import 出**第二个** ContextVar，两边都看不见对方设的值）。
    """
    return _mod("web.request_context")


def _resolution():
    """`web/llm_resolution.py`：解析策略（纯函数）。"""
    return _mod("web.llm_resolution")


def _adapter():
    return _mod("adapters.llm_adapter")


def _caller_ip() -> str | None:
    """当前上下文里的调用方 IP —— 读不到上下文返回 None（不伪造默认值）。"""
    caller = _ctx().LLM_CALLER.get(None)
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


class _Restored:
    """临时改一处**进程级**状态，退出时还原 —— 四对「读口 + 写口」共用这一个。

    入场动作由 *enter* 给出，它同时返回撤销动作；载体差异只在这两行里：
      - **普通全局**（守卫、投递器、载体登记表）：`_snapshot(read, write, value)`
        —— 读口取快照、写口写入，撤销=把快照写回；
      - **ContextVar**（`LLM_CALLER`）：`_set_var(var, value)` —— `set` 返回的 token
        就是撤销句柄。`reset(token)` 连「本来没设过」这个事实一并还原，快照式的
        「写回 None」做不到。

    **还原，而不是置 None / 清空**：这四处状态没有一处是本用例私有的 —— 生产 app
    一跑 lifespan 就注册了守卫与投递器（`set_main_loop`），telemetry 导入期就登记了
    载体。置 None 会把**生产那一份**拆掉：同进程后面依赖投递的用例静默改走跨 loop 的
    回退分支，直接调适配器出站的用例凭空被门管住、报 `LLMCallerMissing` 而不是自己
    那条断言。ContextVar 同理 —— 主线程同一个上下文跨用例存活，设了不还原，后面读
    `LLM_CALLER` 的用例会看到上一条留下的身份：最先中招的是 L7「无上下文 = fail-closed」，
    它读到别人的 IP，于是红成一副与成因无关的样子（且只在全文件跑时红、单跑绿，最难查）。
    """

    def __init__(self, enter) -> None:
        self._enter = enter

    def __enter__(self):
        self._undo = self._enter()
        return self

    def __exit__(self, *exc):
        self._undo()
        return False


def _snapshot(read, write, value) -> _Restored:
    """普通全局的载体：读口 + 写口一对（`get_*` / `set_*` 那对）。"""
    def _enter():
        prev = read()
        write(value)
        return lambda: write(prev)
    return _Restored(_enter)


def _set_var(var, value) -> _Restored:
    """ContextVar 的载体：写口是 `var.set`，它返回的 token 就是撤销句柄 —— 不必读快照。

    名字避开 `_token` —— 那个已是本文件的 JWT 编码辅助。
    """
    def _enter():
        token = var.set(value)
        return lambda: var.reset(token)
    return _Restored(_enter)


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


@pytest.fixture
def seed_user(store):
    """建**真用户行**再写入 API 配置 —— 让 `get_user_api_config` 真读得回来。

    没有 users 行时它返回**全空**配置（`FROM users u LEFT JOIN user_secrets s` 无行），
    而 `update_user_api_config` 是纯 `UPDATE ... WHERE user_id = ?`：不建行、也不报错，
    静默什么都不写。于是 `get_user_llm` 落到**全局回落** —— 它非 None 只因为**本机
    `.env` 恰好有 key**；干净检出与 CI（都不注入 `DEEPSEEK_API_KEY`）上是 None。
    也就是说「解析层拿到实例」这个前提会退化成环境凑巧，而不是用例构造出来的。

    **不并进 `store` 夹具**：写配置要给用户一条 `api_key`，而 L1/L2 的前提恰恰相反
    ——「无 key + 全局可用」，靠 `store.get_user_api_config` 读回空配置成立。夹具替
    每条用例建配置，那两条就从「测全局回落」变成「测用户配置」了。
    """
    def _seed(uid: str, *, base_url: str = "https://api.deepseek.com",
              api_key: str = "k", model: str = "m") -> str:
        asyncio.run(store.create_user(uid, f"u_{uid}", "hash"))
        asyncio.run(store.update_user_api_config(uid, api_key, base_url, model))
        return uid
    return _seed


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

    C2b 起只有一个模块 `core.concurrency` 定义派生原语（telemetry 那份已删，不是转发）。
    **要求调用点用属性形式**（`C.ctx_thread(...)` 而非 `from ... import ctx_thread`），
    否则函数内绑定在 import 期就固化了，这里拦不到 —— 那处绑定同时是「迁移漏了调用点」
    的探子：谁还留着 `from core.telemetry import ctx_thread`，这里就拦不到它、用例红。
    """
    captured: list[tuple] = []

    def _fake_ctx_thread(target, args=(), kwargs=None, **kw):
        captured.append((target, args, kwargs or {}))
        return threading.Thread()

    monkeypatch.setattr(_mod("core.concurrency"), "ctx_thread", _fake_ctx_thread)
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
def test_l3_cache_hit_still_checks_geo(store, seed_user, monkeypatch):
    ctx = _ctx()
    la = _adapter()
    _fake_geo(monkeypatch)
    uid = seed_user(f"u_l3_{uuid.uuid4().hex[:8]}", base_url="https://api.other.com")

    with _set_var(ctx.LLM_CALLER, ctx.Caller(_ALLOWED_IP, uid)):
        first = asyncio.run(deps.get_user_llm(uid, store))   # 入缓存
        assert first is not None, (
            "前提破了：解析出口没拿到用户实例（users 行没建出来？）—— "
            "这条要证的是缓存热也过 preflight，不是「解析拿到实例」")
        assert first.base_url == "https://api.other.com", (
            f"解析拿到的不是这条用户配置里的实例：{first.base_url!r}")

    with _set_var(ctx.LLM_CALLER, ctx.Caller(_BLOCKED_IP, uid)):   # 换 IP，缓存仍热
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


def test_l4_live_session_is_blocked_before_outbound(store, seed_user, monkeypatch):
    """F6 的形状：**会话内存里那个实例**也要被拦，光拦解析出口拦不住它。

    构造上有意让**解析层放行、调用层拦**，否则这条会退化成测「解析时的提前 403」：
      - 用户配置的 `base_url` 是白名单内的（`deepseek`），且 `chat.send` 的解析出口
        （`get_user_llm`）**不再收 client_ip**（§2.8）—— 于是解析这条路全程不判 geo；
      - 会话里那个 `_SessionEngine` 拿着的适配器 `base_url` 是**非白名单**的，
        且客户端已换成毒对象 —— 只有调用点门能拦住它。

    「解析层放行」是**发请求前断言**出来的前提（`seed_user` 建出 users 行，让
    `get_user_llm` 真读回那条配置），不是环境凑巧：没有 users 行时它落到全局回落，
    而回落非 None 只因为本机 `.env` 恰好有 key —— 干净检出与 CI 上就是 503。
    前提破了必须红在前提那两行，不许变成走了别的路的 403 或 503。

    应用要按 §2.6 装配（`install_llm_gate` + 领域异常出口），否则 `LLMCallRefused`
    会被 `_do_chat` 之外的路径吞成 500 而不是 403。"""
    gate = _gate()
    la = _adapter()
    _fake_geo(monkeypatch)
    uid = seed_user(f"u_live_{uuid.uuid4().hex[:8]}")   # 白名单内：解析层必放行

    # ── 前提（发请求之前）────────────────────────────────────────────────
    import web.geo_guard as G

    resolved = asyncio.run(deps.get_user_llm(uid, store))
    assert resolved is not None, (
        "前提破了：解析出口没拿到用户实例（users 行没建出来？）—— 请求会在 "
        "`chat.send` 那道 503 就返回，根本走不到门")
    # 见证「拿到的是**用户那条**配置，不是全局回落」：没有它，本机恰好有 key 时
    # 回落也非 None、base_url 也恰是 deepseek，前提破了这条用例仍会绿 —— 构造就还是
    # 环境凑巧。model 由 `seed_user` 显式给，回落的那个来自 config.yaml，两者不同。
    assert resolved.model == "m", (
        f"前提破了：解析出口走的是**全局回落**那条路（model={resolved.model!r}），"
        "不是这条用户配置 —— 回落只是让 503 放行，证不到调用点门")
    allowed, why = G.check_api_allowed(_BLOCKED_IP, resolved.base_url)
    assert allowed, (
        f"前提破了：用户配置的 base_url {resolved.base_url!r} 在 {_BLOCKED_IP} 下不被放行"
        f"（{why}）—— 解析层就先 403 了，这条测不到调用点门")

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

        # 装配由本用例自己声明（不靠 import 副作用），且**退出时还原** —— 守卫是
        # 进程级全局，装了不撤，本进程后面每条直接调适配器出站的用例都会凭空被门
        # 管住。`_snapshot` 的入场快照就承担还原这一半。
        with _snapshot(la.get_call_guard, la.set_call_guard, None):
            gate.install_llm_gate(app)
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


@pytest.mark.parametrize("name", _outbound_names())
def test_l6_every_outbound_method_hits_the_guard(name):
    la = _adapter()
    with _snapshot(la.get_call_guard, la.set_call_guard, lambda base_url: _REFUSAL):
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


def test_l6_non_call_set_matches_the_class():
    """反向校验：`_NON_CALL` 里每个名字都得真在类上 —— 陈旧条目会让「哪些不是出站」
    悄悄失真，而方法集正是靠它算的。另一向（非空集、逐个被拦）由上面两条钉住。"""
    for n in _NON_CALL:
        assert hasattr(LLMAdapter, n), f"_NON_CALL 里的 {n} 在 LLMAdapter 上不存在（名单陈旧）"


# ── L7：无上下文 fail-closed ──────────────────────────────────────────────

def test_l7_missing_context_fails_closed_and_system_passes():
    gate = _gate()
    ctx = _ctx()
    la = _adapter()

    with pytest.raises(gate.LLMCallerMissing):
        gate.geo_call_guard("https://api.deepseek.com")

    with ctx.system_llm_context():
        assert gate.geo_call_guard("https://api.deepseek.com") is None

    # 同一件事在**出站方法**那一侧也要成立 —— 守卫是注入进适配器的。
    with _snapshot(la.get_call_guard, la.set_call_guard, gate.geo_call_guard):
        adapter = LLMAdapter(api_key="k", base_url="https://api.deepseek.com")
        with pytest.raises(gate.LLMCallerMissing):
            adapter._before_call()
        with ctx.system_llm_context():
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


@pytest.mark.parametrize("otel", [False, True], ids=["otel-off", "otel-on"])
@pytest.mark.parametrize(
    "where", ["endpoint", "stream", "public", "thread", "submit", "nested", "asyncio_run"])
def test_l8_contextvar_visible_everywhere(where, otel, monkeypatch):
    """C2b 翻转（原 `xfail(strict=True)`）：传播原语收敛为一个，contextvar 处处可见。

    两维都要「开关无关」：`ctx_thread`/`ctx_submit` 现在的捕获/恢复不经过载体以外
    任何东西，OTEL 开或关只决定 OTel 那一个载体是不是 no-op —— contextvar 这一半
    与开关无关（迁移前的 `threading.Thread(…)` 退化路径已不存在）。
    """
    from core import telemetry as T

    monkeypatch.setattr(T, "_ENABLED", otel)

    if where in ("endpoint", "stream", "public"):
        monkeypatch.setattr(server, "get_storage", lambda: _AuthStore("u_l8"))
        observed = _via_http(where)
    else:
        ctx = _ctx()
        with _set_var(ctx.LLM_CALLER, ctx.Caller(_BLOCKED_IP, "u_l8")):
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

    ctx = _ctx()
    app = FastAPI()
    server.register_domain_error_handlers(app)
    # 门的装配由调用方声明并还原（见 `_boom` 里的 `_snapshot(...)` 段），此处不装。

    class _Who(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = {"id": uid}
            ctx.LLM_CALLER.set(ctx.Caller(request.headers.get("X-Real-IP", ""), uid))
            return await call_next(request)

    app.add_middleware(_Who)

    @app.get("/boom")
    def boom():
        """真走一次出站：拒绝由**门**产生，审计也由门记。

        就地 `raise LLMCallRefused(...)` 会绕开守卫 —— 那样三条用例里关于审计的两条
        落在一条生产上不存在的路径上，恒真。适配器客户端是毒对象，所以「门没挡住」
        会以 AssertionError 现形，而不是悄悄发出去。
        """
        llm = _poison(LLMAdapter(api_key="k", base_url="https://api.other.com"))
        return {"ok": llm.chat("s", [{"role": "user", "content": "hi"}])}

    return app


def _boom(uid: str = "u9"):
    """按生产函数装门 + 发一次 `/boom`，装完还原（守卫是进程级全局）。

    还原是必须的：本文件是全仓唯一装门的地方，装了不撤，同一进程里后面**每条**
    直接调适配器出站的用例（适配器单测、路由测试）都会凭空被门管住，报
    `LLMCallerMissing` 而不是它们自己那条断言 —— 那正是「import 期注册」被根除
    掉的那个副作用，只是搬到了测试侧。`_snapshot` 的入场快照承担还原。
    """
    gate = _gate()
    la = _adapter()
    app = _audit_app(uid)
    with _snapshot(la.get_call_guard, la.set_call_guard, None):
        gate.install_llm_gate(app)
        return TestClient(app, raise_server_exceptions=False).get(
            "/boom", headers={"X-Real-IP": _BLOCKED_IP})


def test_l9_blocked_maps_to_403_with_the_reason(monkeypatch):
    _fake_geo(monkeypatch)
    store = _AuditStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    sub = _Submitter()
    S = _mod("core.scheduling")
    with _snapshot(S.get_loop_submitter, S.set_loop_submitter, sub):
        r = _boom()

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "境内不支持境外模型", r.text
    assert len(sub.calls) == 1, f"拒绝没有经投递原语发出去：{sub.calls}"
    assert sub.calls[0][1] is False, "审计投递必须是 wait=False（不阻塞请求）"


def test_l9_audit_is_delivered_once_with_the_caller(monkeypatch):
    _fake_geo(monkeypatch)
    store = _AuditStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    S = _mod("core.scheduling")
    with _snapshot(S.get_loop_submitter, S.set_loop_submitter, _Submitter()):
        r = _boom()

    assert r.status_code == 403, r.text
    assert len(store.calls) == 1, f"审计次数不对：{store.calls}"
    uid, ip, base_url, reason = store.calls[0]
    assert uid == "u9" and ip == _BLOCKED_IP, f"审计的身份不是 Caller 里的：{store.calls[0]}"
    assert base_url == "https://api.other.com" and reason.strip()


def test_l9_audit_failure_keeps_the_403(monkeypatch):
    _fake_geo(monkeypatch)
    store = _AuditStore(boom=True)
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    sub = _Submitter()
    S = _mod("core.scheduling")
    with _snapshot(S.get_loop_submitter, S.set_loop_submitter, sub):
        r = _boom()

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

def test_l11_app_installs_the_production_guard():
    """锚点在**启动那一刻**，不在 import 那一刻。

    `import server` 只导入模块，不改变任何进程级策略 —— 装配（守卫、投递器、主 loop）
    都在 lifespan 里，`with TestClient(app)` 才跑它。用生产 app 本身触发，才证得了
    「生产经 lifespan 装上了门」；锚在 import 期只会证「模块被导入过」，而那个副作用
    本身就是本步要根除的东西（它让不启 app 的适配器单测凭空被门管住）。
    """
    gate = _gate()
    la = _adapter()
    getter = getattr(la, "get_call_guard", None)
    assert getter is not None, "adapters.llm_adapter 没发布与 set_call_guard 配对的读取口"
    # 从「未注册」起步：守卫是进程级全局，同会话里别的用例可能已经装过 —— 不清空的话
    # 这里读到的是**别人留下的**注册，删掉 lifespan 那一行也照样绿（恒真的假锁）。
    with _snapshot(la.get_call_guard, la.set_call_guard, None):
        assert getter() is None, "起点不是未注册 —— 前面有用例装过守卫"
        with TestClient(server.app):
            assert getter() is gate.geo_call_guard, (
                "生产 app 启动后守卫不是策略层那一个 —— lifespan 没装门")


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


def test_l13_core_and_adapters_do_not_import_web():
    """core/ 与 adapters/ 里不得出现指向 web 层（deps/web/routers）的 import。

    import 语句本身就是事实，不需要推断 —— 这是「唯一允许向下依赖」那条不变量
    的可判定形式。**函数体内的局部 import 也算**：改注入之前，那 10 处反向依赖
    全是这么写的，只看模块级 import 会整类漏掉。
    """
    hits = [x for x in _scan_prod_imports()]
    assert not hits, (
        f"core/adapters 里还有 {len(hits)} 处指向 web 层的 import：\n" +
        "\n".join(f"  {rel}:{ln}  {text}" for rel, ln, text, _ in hits))


# ── L14：跨 loop 投递的唯一原语 ───────────────────────────────────────────

def test_l14_submit_delegates_when_registered():
    S = _mod("core.scheduling")
    seen: list[tuple] = []

    def _recorder(coro, *, wait: bool = True, timeout: float | None = None):
        seen.append((coro, wait))
        coro.close()
        return "DELEGATED"

    async def _noop():
        return "MINE"

    with _snapshot(S.get_loop_submitter, S.set_loop_submitter, _recorder):
        got = S.submit_to_main_loop(_noop(), wait=False)

    assert got == "DELEGATED", f"已注册时没有委托给注册实现（拿到 {got!r}）"
    assert seen and seen[0][1] is False, f"wait 没透传：{seen}"


def test_l14_unregistered_fallback_semantics():
    S = _mod("core.scheduling")

    async def _value():
        return "RAN"

    with _snapshot(S.get_loop_submitter, S.set_loop_submitter, None):
        # wait=True：阻塞取结果，且响亮（发 warning）
        with pytest.warns(Warning):
            assert S.submit_to_main_loop(_value()) == "RAN"

        # wait=False：当前线程有运行中的 loop → 不阻塞，但要真的跑起来
        order: list[str] = []

        async def _probe():
            await asyncio.sleep(0)  # 让出一次：只要「返回」发生在协程跑完之前
            order.append("CORO")

        async def _driver():
            task = S.submit_to_main_loop(_probe(), wait=False)
            order.append("RETURNED")
            # 拿到什么就 await 什么 —— 回退有两种合法实现（create_task / asyncio.run），
            # 直接裸调会让「协程根本没排上」也过关。
            if task is not None:
                await task

        asyncio.run(_driver())

    # 两条都要：丢了协程（order 缺 CORO）、或阻塞到跑完才返回（顺序颠倒）都算错。
    assert order == ["RETURNED", "CORO"], (
        f"wait=False 的回退语义不对（期望先返回后跑完，实得 {order}）："
        "要么协程被丢了，要么它其实阻塞了")


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

    with _snapshot(S.get_loop_submitter, S.set_loop_submitter, _recorder):
        _mod("core.utils").try_record_usage(_Storage(), "u14", _LLM(), action="chat")

    assert seen, "记账没有经投递原语出去（还在自建 loop）"
    assert seen[0][1] is False, f"记账投递必须是 wait=False：{seen}"


# ── L15：传播模块不碰 opentelemetry；载体协议被派生路径驱动 ───────────────

_OTEL_MODULES = ("opentelemetry",)


def test_l15_concurrency_scan_face_is_not_empty():
    """非空守卫：文件不在/扫不到时，「不含 opentelemetry」会假绿（扫了个空气）。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    concurrency = root / "core" / "concurrency.py"
    assert concurrency.is_file(), "core/concurrency.py 还不存在 —— 扫描面为空"
    assert concurrency.read_text(encoding="utf-8").strip(), "core/concurrency.py 是空文件"


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


def test_l15_registry_read_port_is_populated_and_returns_a_copy():
    """读口：`get_context_carriers()` 看得见 telemetry 登记的 OTel 载体，且给的是副本。

    两半都要，缺一半都会让本组用例失去意义：
      - 空表 → 下面「载体被派生路径驱动」的用例只是在自己登记的假载体上打转，
        生产那个载体接没接上完全没被观测；
      - 返回本体（不是副本）→ 用例 `clear()` 一下就能把生产载体悄悄关掉，
        而「关掉后仍绿」会让任何依赖载体的判据失真。
    """
    C = _mod("core.concurrency")
    carriers = C.get_context_carriers()
    names = [f"{type(c).__module__}.{type(c).__name__}" for c in carriers]
    assert carriers, "登记表是空的 —— telemetry 没登记载体，读口观测不到任何东西"
    assert any(type(c).__module__ == "core.telemetry" for c in carriers), (
        f"telemetry 没登记 OTel 载体，表里只有：{names}")

    carriers.clear()
    assert C.get_context_carriers(), "get_context_carriers() 返回的不是副本"


def test_l15_carrier_protocol_is_driven_by_the_derive_path():
    """载体协议被驱动 = 一次 `ctx_thread` 里 capture → restore → release 按序发生。

    本条走**裸线程**入口；`ctx_submit`（线程池）入口的同一件事、以及「三者同处一个
    `ctx.run`」的结构判据在下面两条 —— 两个派生入口各钉一次，不互相代替。
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

    box: list = []
    with _snapshot(C.get_context_carriers, C.set_context_carriers,
                   [*C.get_context_carriers(), _Carrier()]):
        t = C.ctx_thread(box.append, args=("ran",))
        t.start()
        t.join(timeout=5)

    assert box == ["ran"], "派生体没跑"
    assert log[:1] == ["capture"], f"调用方线程没先 capture：{log}"
    assert "restore" in log and "release" in log, f"子线程没按序 restore/release：{log}"
    assert log.index("restore") < log.index("release"), f"顺序反了：{log}"


class _StructuralCarrier:
    """假载体，判据借用**标准库自己的同 Context 约束**：`ContextVar.set` 返回的 token
    只在产生它的那个 Context 里 `reset` 得掉，换个 Context 就 `ValueError`。

    为什么不能只断言「不抛异常」：那是错的判据，B3 已证 —— OTel 的 `detach` 遇到
    token 属另一 Context 时把它**吞掉**，只留一条 "Failed to detach context" 日志，
    调用方什么也看不到。真实后果（父上下文一直挂在拷贝出的 Context 里没释放）因此
    无声无息。本载体不吞，位置错了就必然把 ValueError 冒到 `fut.result()`。

    三步都记 `(名字:动作, 线程号)`，于是「capture 在调用方线程、restore/release 在
    跑 fn 的那条线程」以及「release 逆序配对」也是从事实读出来的，不靠注释声明。
    """

    def __init__(self, name: str, log: list) -> None:
        self.name = name
        self._log = log          # 两个载体共用一份：各自的顺序要能互相对照
        self._var: contextvars.ContextVar[str] = contextvars.ContextVar(f"l15_{name}")

    def _note(self, what: str) -> None:
        self._log.append((f"{self.name}:{what}", threading.get_ident()))

    def capture(self):
        self._note("capture")
        return None

    def restore(self, state):
        self._note("restore")
        return self._var.set("attached")

    def release(self, token):
        self._note("release")
        self._var.reset(token)      # 不在同一个 Context → ValueError


def test_l15_carrier_restore_and_release_share_one_context_run():
    """**结构判据**：restore 与 release 必须同处一个 `ctx.run`，且逐一逆序配对。

    要打红的变异：把 release 提到 `ctx.run(...)` 之外 —— 「token 属另一个 Context」
    的失败在 OTel 那里被吞、在假载体这里现形成 ValueError。
    """
    C = _mod("core.concurrency")
    log: list[tuple[str, int]] = []
    a, b = _StructuralCarrier("a", log), _StructuralCarrier("b", log)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with _snapshot(C.get_context_carriers, C.set_context_carriers,
                       [*C.get_context_carriers(), a, b]):
            fut = C.ctx_submit(pool, threading.get_ident)
            worker = fut.result(timeout=5)   # release 逸出 ctx.run → ValueError 从这里冒
            caller = threading.get_ident()
    finally:
        pool.shutdown(wait=True)

    assert worker != caller, "没起线程池工人 —— 下面的线程判据全是空转"
    assert [n for n, _ in log] == [
        "a:capture", "b:capture", "a:restore", "b:restore", "b:release", "a:release",
    ], f"三步的先后 / 配对不对（restore 顺登记序、release 逆序）：{log}"
    assert [tid for _, tid in log] == [caller, caller, worker, worker, worker, worker], (
        f"capture 该在调用方线程、restore/release 该在跑 fn 的那条线程：{log}")


def test_l15_carrier_release_pairs_on_the_exception_path():
    """`fn` 抛异常时 release 仍要发生，且仍在**同一个** `ctx.run` 内（try/finally 配对）。

    少了 finally，载体就永远不复位 —— 而这条路径上没人会注意到，直到下次派生撞上
    残留状态。release 位置错了同样现形：ValueError 会盖过 RuntimeError。
    """
    C = _mod("core.concurrency")
    log: list[tuple[str, int]] = []
    carrier = _StructuralCarrier("x", log)

    def _boom():
        raise RuntimeError("派生体自己炸了")

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with _snapshot(C.get_context_carriers, C.set_context_carriers,
                       [*C.get_context_carriers(), carrier]):
            with pytest.raises(RuntimeError, match="派生体自己炸了"):
                C.ctx_submit(pool, _boom).result(timeout=5)
    finally:
        pool.shutdown(wait=True)

    assert [n for n, _ in log] == ["x:capture", "x:restore", "x:release"], (
        f"异常路径上 release 没配对发生：{log}")


def test_l15_otel_context_still_propagates_into_a_derived_thread(monkeypatch):
    """行为基线（B4/B5）：OTEL 开启时，派生线程里仍看得见调用方 attach 的 OTel Context。

    迁到共用包装器后这一条不许退化 —— 载体**无条件**登记，开关的差异由载体自己表达
    （`capture` 在关上时返回 None，于是三步全 no-op）。这里用 `opentelemetry.context`
    直接做观测点：它不依赖 tracer provider，装的只是「当前上下文里挂着什么」。
    """
    from core import telemetry as T

    C = _mod("core.concurrency")
    otel_ctx = _mod("opentelemetry.context")
    monkeypatch.setattr(T, "_ENABLED", True)

    key = "l15_marker"
    box: list = []

    token = otel_ctx.attach(otel_ctx.set_value(key, "propagated", otel_ctx.get_current()))
    try:
        t = C.ctx_thread(lambda: box.append(otel_ctx.get_value(key)))
        t.start()
        t.join(timeout=5)
    finally:
        otel_ctx.detach(token)

    assert box == ["propagated"], f"OTEL 开启时上下文没跟过派生线程：{box}"
