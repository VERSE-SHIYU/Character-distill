# -*- coding: utf-8 -*-
"""缺陷 35 的锁：身份传得进派生面、写得只有一处、且账真的落库。

**为什么原来的两条锁看不见它。** `test_usage_accounting_lock` 锁的是「LLM 调用点在
静态调用图上都能走到唯一记账出口」—— 它按**接收者名字**（`llm` / `*_llm`）认调用点，
而 `/start` 全部 LLM 工作挂在 `distiller` 接收者后面，那批调用点不在它的扫描面内；
且它只看调用图可达，从不看 `storage`/`user_id` 的值。`test_distill_usage_accounting`
把出口整个换成 `list.append`，用**不带身份**的 Distiller 跑，断言的是「出口被触发、
action 对」—— 出口在参数为空时照样被调用，只是自己 early-return。

**本锁补的那一维**：身份**值**是否到位、是否落在库里。分四层，各锁一维：

  1. 派生面传播 —— `ctx_thread` / `ctx_submit` 派生出去之后还读不读得到（各一条）；
  2. 写口唯一 —— 全仓 AST 现算：`LLM_CALLER.set` 的调用点只有两处，都在既定出口上
     （`AuthMiddleware.dispatch` 与 `core.request_context.system_llm_context`）；
  3. 形态 —— `Distiller` 不再持有 `_user_id` 实例属性；`web/` 里不再有对 distiller
     实例写身份的那两行（「两条路各写一份」的复发形态）；
  4. 落库 —— 真 app + 真 `AuthMiddleware` + 真 `/start` 路由，断言 `usage_stats`
     行数 == 出口发出笔数，且 `user_id` 是请求身份。

**身份只有一份。** 读口是 `core.request_context.current_user_id()`，它读的就是门用的
那个 `LLM_CALLER` —— **不是**第二个 ContextVar。这条是本案返工的由来：第一版在
`core/` 另建了一个只装 user_id 的 ContextVar，中间件于是同一个出口连写两份同一个
值。`web/` 没有 `__init__.py`，两个同名 ContextVar 会各看各的，这份「重复」迟早
分叉成两份事实。

**已登记的边界（不是遗漏）。** 第 2 条的扫描面是 `core/` + `web/` + `adapters/`，
**不含 `tests/`**：用例本身要设上下文来观察传播，那是观察装置不是写口。第 4 条只
覆盖 MapReduce 那条分支（短路成单次 longcontext 会变成 3 笔），也只断言「笔数对得上」
—— token 数值是否精确不在这里，那是 `test_llm_access_gate` 的用量记账面。第 2/3 条是
**形锁**：它们能挡住「又写一处」，但挡不住「把身份改从参数传」这类整体换形 —— 那种
变更会先打红第 4 条。
"""
from __future__ import annotations

import ast
import asyncio
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import deps
import server
from conftest import TEST_JWT_SECRET
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import distill as D
from routers.auth import JWT_ALGORITHM, get_current_user
from deps import get_storage
from storage.sqlite_store import SQLiteStore
from core import concurrency as C
from core import scheduling as S
from core.request_context import Caller, LLM_CALLER, current_user_id

_REPO = Path(__file__).resolve().parent.parent


def _token(uid: str) -> str:
    import jwt

    return jwt.encode({"sub": uid}, TEST_JWT_SECRET, algorithm=JWT_ALGORITHM)


def _py_files(*roots: Path) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        out += [p for p in root.rglob("*.py")
                if ".venv" not in p.parts and "node_modules" not in p.parts]
    return out


# ── 1. 派生面传播 ──────────────────────────────────────────────────────────


def test_identity_reaches_ctx_thread():
    tok = LLM_CALLER.set(Caller(ip=None, user_id="u_thread"))
    seen: list[str | None] = []

    # 断言写在**主线程**：线程体里抛的异常只进 stderr，用例照样绿 —— 那样这条锁
    # 在裸 threading.Thread 下不会响（实测踩过）。
    try:
        t = C.ctx_thread(lambda: seen.append(current_user_id()))
        t.start()
        t.join(timeout=10)
    finally:
        # **必须还原**：`LLM_CALLER` 就是门读的那个 ContextVar（身份只有一份），
        # 留在本线程上下文里会污染同进程后面的门用例 —— 它们断言「没有上下文时
        # fail-closed」，读到的却是我这一笔（实测：不还原则 L7 红）。
        LLM_CALLER.reset(tok)
    assert seen == ["u_thread"], f"ctx_thread 派生面读到 {seen} —— contextvar 没被拷过去"


def test_identity_reaches_ctx_submit():
    tok = LLM_CALLER.set(Caller(ip=None, user_id="u_pool"))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert C.ctx_submit(pool, current_user_id).result(timeout=10) == "u_pool"
    finally:
        LLM_CALLER.reset(tok)


# ── 2. 写口唯一 ────────────────────────────────────────────────────────────


def _identity_writes(paths: list[Path]) -> list[str]:
    """全仓现算：`LLM_CALLER.set(...)` 的调用点，返回 `file:line`。

    判据是**接收者名字 + 方法名**（`LLM_CALLER` / `set`），不是某一处的临时函数名 ——
    写口换一层包装、或有人在路由里直接 `LLM_CALLER.set(Caller(...))` 伪造身份，
    点的都是同一个 ContextVar，都会被抓到。
    """
    hits: list[str] = []
    for p in paths:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "set" \
                    and isinstance(f.value, ast.Name) and f.value.id == "LLM_CALLER":
                hits.append(f"{p.relative_to(_REPO).as_posix()}:{n.lineno}")
    return sorted(hits)


def test_only_the_two_declared_exits_write_identity():
    hits = _identity_writes(_py_files(_REPO / "core", _REPO / "web", _REPO / "adapters"))
    allowed = sorted([f"core/request_context.py:{_system_context_identity_line()}",
                      f"web/server.py:{_middleware_identity_line()}"])
    assert hits == allowed, (
        f"身份写口不唯一：{hits}，只应是 {allowed}。身份只有 `LLM_CALLER` 这一份，"
        "写它的只应是 `web/server.py` 的 AuthMiddleware（请求内，单一 call_next 出口前"
        "设一次）与 `core/request_context.system_llm_context`（请求外，显式声明）—— "
        "多一处写口就是又多了一条「得记得写」的路，或一条能伪造身份的路。")


def _llm_caller_set_line(src: str, scopes: list) -> int:
    """在给定 AST 结点的子树里找那一行 `LLM_CALLER.set(...)` 的**源码行号**。"""
    for scope in scopes:
        for n in ast.walk(scope):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "set" and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == "LLM_CALLER":
                return n.lineno
    raise AssertionError("没找到 LLM_CALLER.set —— 身份根本没被设过")


def _middleware_identity_line() -> int:
    """`AuthMiddleware.dispatch` 里那一行 `LLM_CALLER.set(...)` 的行号。"""
    src = (_REPO / "web" / "server.py").read_text(encoding="utf-8")
    for cls in [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ClassDef)]:
        if cls.name == "AuthMiddleware":
            dispatch = [n for n in cls.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name == "dispatch"]
            return _llm_caller_set_line(src, dispatch)
    raise AssertionError("web/server.py 里没有 AuthMiddleware 类")


def _system_context_identity_line() -> int:
    """`system_llm_context` 里那一行 `LLM_CALLER.set(...)` 的行号。"""
    src = (_REPO / "core" / "request_context.py").read_text(encoding="utf-8")
    for fn in [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)]:
        if fn.name == "system_llm_context":
            return _llm_caller_set_line(src, [fn])
    raise AssertionError("core/request_context.py 里没有 system_llm_context —— 请求外的那条出口没了")


def test_identity_write_scan_is_not_vacuous():
    """负控：扫描器对**合成**出来的第三处写口必须报得出来 —— 否则上一条是在假绿。"""
    probe = _REPO / "e2e" / "scratch" / "_synthetic_write_probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("LLM_CALLER.set(SYSTEM)\n", encoding="utf-8")
    try:
        hits = _identity_writes([probe])
    finally:
        probe.unlink(missing_ok=True)
    assert len(hits) == 1, f"扫描器漏掉了合成写口（{hits}）—— 判据面失效"


# ── 3. 形态 ────────────────────────────────────────────────────────────────


def test_distiller_holds_no_user_identity_attribute():
    tree = ast.parse((_REPO / "core" / "distiller.py").read_text(encoding="utf-8"))
    found = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "_user_id"
             and isinstance(n.value, ast.Name) and n.value.id == "self"]
    assert not found, (
        f"core/distiller.py 第 {found} 行又出现了 self._user_id —— 身份一旦挂回实例属性，"
        "「谁忘了注入谁就静默不记」这个形态就回来了。身份走 core.request_context 的上下文。")


def test_no_identity_write_on_distiller_instances():
    """`web/` 里不得再对 distiller 实例写身份 —— 那正是 `/start` 漏、`/run_stream` 独有注入的形态。"""
    offenders: list[str] = []
    for p in _py_files(_REPO / "web"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(n, ast.Assign):
                continue
            for t in n.targets:
                if isinstance(t, ast.Attribute) and t.attr in ("_user_id", "_storage") \
                        and isinstance(t.value, ast.Name) and "distiller" in t.value.id:
                    offenders.append(f"{p.relative_to(_REPO).as_posix()}:{t.lineno}")
    assert not offenders, (
        f"又往 distiller 实例上写身份：{offenders}。storage 由唯一装配出口 "
        "`web/deps.get_distiller` 注入，身份由上下文带 —— 路由侧不该有任何一行。")


# ── 4. 中间件真的在出口前设了 ────────────────────────────────────────────────


class _StubStore:
    """只实现中间件真正调到的两个方法。

    不用真 `SQLiteStore`：它会把一条 aiosqlite 连接绑到建它时的那个 loop 上，而一个
    pytest 进程里同时活着的 loop 不止一个（TestClient 每个请求各起一个 portal）——
    连接被跨 loop 用就是那条偶发的 `RuntimeError: Event loop is closed`。中间件要的
    只是「这个 user_id 存不存在」，用不着库。
    """

    def __init__(self, uid: str):
        self.uid = uid

    async def get_user_by_id(self, user_id: str) -> dict | None:
        return {"id": self.uid, "is_disabled": False} if user_id == self.uid else None

    async def update_last_active(self, user_id: str) -> None:
        pass


def test_auth_middleware_sets_identity_for_downstream(monkeypatch):
    app = FastAPI()
    app.add_middleware(server.AuthMiddleware)
    uid = "u_ident"

    @app.get("/api/probe")
    def probe_api():
        return {"uid": current_user_id()}

    @app.get("/probe_public")
    def probe_public():
        return {"uid": current_user_id()}

    # 改的是 `deps._storage` 那个**全局**、不是某个模块的 `get_storage` 绑定：
    # `server` / `routers.*` 都是 `from deps import get_storage`，拿到的是同一个函数
    # 对象，它读的就是这个全局。改绑定只能改一处，改全局每处都跟着走。
    monkeypatch.setattr(deps, "_storage", _StubStore(uid))
    client = TestClient(app)
    authed = client.get("/api/probe", headers={"Authorization": f"Bearer {_token(uid)}"})
    public = client.get("/probe_public")
    assert authed.json()["uid"] == uid, "带 token 的 /api/ 路径下游读不到身份"
    assert public.json()["uid"] is None, "公开路径不该有身份（匿名 ≠ 上一次请求的残留）"


# ── 5. 落库：真 app + 真中间件 + 真 /start 路由 ──────────────────────────────

IDENTIFY_JSON = '[{"name": "甲", "aliases": []}]'
CARD_JSON = '{"name": "甲", "identity": "测试", "personality_traits": ["寡言"]}'


class _FakeClient:
    async def close(self):
        pass


class _FakeLLM:
    def __init__(self):
        self._model = "lock-model"
        self.last_usage = {"prompt_tokens": 11, "completion_tokens": 7}

    @property
    def model(self) -> str:
        return self._model

    def chat(self, system, messages, max_tokens=None):
        self.last_usage = {"prompt_tokens": 11, "completion_tokens": 7}
        if "识别" in system:
            return IDENTIFY_JSON
        if "整合" in system:
            return "一份档案草稿：甲很沉默。"
        if "人格档案" in system:
            return CARD_JSON
        return "[]"

    def chat_stream(self, system, messages, max_tokens=None):
        self.last_usage = {"prompt_tokens": 13, "completion_tokens": 9}
        text = CARD_JSON if "角色卡" in messages[0]["content"] else "一份档案草稿。" * 4
        for i in range(0, len(text), 40):
            yield text[i:i + 40]

    def _make_async_client(self):
        return _FakeClient()

    async def async_chat(self, system, messages, client=None):
        return "甲很沉默，说了一句话。", {"prompt_tokens": 21, "completion_tokens": 5}


class _LoopSubmitter:
    """生产里 `web/deps.set_main_loop` 注册的那个东西的等价物（专属 loop 线程）。

    没有它，出口的 `wait=False` 会在蒸馏线程那个临时 loop 里 `create_task`，
    loop 一关任务被取消、账写不进库 —— 那是测试装置的假象，不是被测行为。
    """

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self._prev = S.get_loop_submitter()
        S.set_loop_submitter(self._submit)
        return self

    def _submit(self, coro, *, wait=True, timeout=600):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout) if wait else fut

    def run(self, coro):
        """在**这个** loop 上跑一段建库/播种 —— 库连接从此绑在这个 loop 上。

        用 `asyncio.run(...)` 播种会让 aiosqlite 的连接绑在一个随即关闭的临时 loop
        上，之后它向那个 loop 回报结果时就撞 "Event loop is closed"（全量跑时偶发）。
        """
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=60)

    def __exit__(self, *exc):
        S.set_loop_submitter(self._prev)
        # **不关这个 loop**：临时库的 aiosqlite 工作线程手里还攥着挂在上面的 future，
        # 关掉 loop 之后它会 `call_soon_threadsafe` 撞上 "Event loop is closed"，
        # 变成全量跑时才偶发的 PytestUnhandledThreadExceptionWarning。线程是 daemon，
        # 进程退出时自己会走。
        return False


def _usage_rows(db: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT action, user_id FROM usage_stats ORDER BY rowid").fetchall()
    finally:
        conn.close()


def test_start_route_lands_usage_rows(monkeypatch):
    """`/start` 走真中间件 + 真后台线程：出口发出几笔，库里就该有几行、且归属请求身份。"""
    uid = "u_start_lock"
    db = _REPO / "e2e" / "scratch" / f"_start_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(str(db))
    tid = f"txt_{uuid.uuid4().hex[:8]}"

    llm = _FakeLLM()
    real_cls = deps.Distiller

    def _factory(l, **kw):                      # 真 Distiller 类，只压掉 longctx 门
        d = real_cls(l, **kw)
        d._longctx_threshold = 1
        d._chunk_size = 200
        return d

    async def _resolve(user_id, storage=None):
        return llm

    monkeypatch.setattr(deps, "Distiller", _factory)
    monkeypatch.setattr(deps, "get_user_llm", _resolve)
    monkeypatch.setattr(deps, "_storage", store)

    app = FastAPI()
    app.include_router(D.router)
    app.add_middleware(server.AuthMiddleware)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "t", "is_admin": False,
    }

    from core.distiller import _IDENTIFY_CACHE

    # 抓住 `/start` 起的那个后台线程，跑完 join 它 —— 不等它，用例退出时它还在收尾，
    # 解释器一关就变成 "cannot schedule new futures after interpreter shutdown" 之类
    # 的告警噪声。`D.C` 就是 `core.concurrency` 模块本身，打一处即可。
    threads: list[threading.Thread] = []
    real_ctx_thread = C.ctx_thread

    def _spy_ctx_thread(target, args=(), **kw):
        t = real_ctx_thread(target, args=args, **kw)
        threads.append(t)
        return t

    monkeypatch.setattr(C, "ctx_thread", _spy_ctx_thread)

    _IDENTIFY_CACHE.clear()
    try:
        with _LoopSubmitter() as sub:
            sub.run(store.create_user(uid, uid, "probe-hash"))
            sub.run(store.save_text(tid, "src.txt", "甲说了一句话。" * 40, user_id=uid))
            r = TestClient(app).post(
                "/api/distill/start",
                json={"text_id": tid, "character_name": "甲", "force": True},
                headers={"Authorization": f"Bearer {_token(uid)}"},
            )
            assert r.status_code == 200, f"/start 没跑起来：{r.status_code} {r.text[:200]}"
            for t in threads:
                t.join(timeout=120)
            rows = _wait_for_rows(str(db), expected=5)
    finally:
        C.ctx_thread = real_ctx_thread
        _IDENTIFY_CACHE.clear()
        try:                        # 同上：SQLiteStore 还攥着句柄，Windows 上删不掉
            db.unlink()
        except OSError:
            pass

    actions = [a for a, _ in rows]
    assert len(rows) == 5, f"落库 {len(rows)} 行（{actions}）—— 出口发出 5 笔却只落这么些"
    assert all(u == uid for _, u in rows), f"落库归属不是请求身份：{rows}"
    assert set(actions) == {
        "distill_identify", "distill_map", "distill_reduce", "distill_format", "distill_autotag",
    }, f"落库的 action 面不对：{actions}"


def _wait_for_rows(db: str, expected: int, timeout: float = 40.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = _usage_rows(db)
        if len(rows) >= expected:
            return rows
        time.sleep(0.25)
    return _usage_rows(db)
