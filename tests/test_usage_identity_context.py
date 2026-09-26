# -*- coding: utf-8 -*-
"""缺陷 35 的锁：身份传得进派生面、写得只有一处、且账真的落库。

**为什么原来的两条锁看不见它。** `test_usage_accounting_lock` 锁的是「LLM 调用点在
静态调用图上都能走到唯一记账出口」—— 它按**接收者名字**（`llm` / `*_llm`）认调用点，
而 `/start` 全部 LLM 工作挂在 `distiller` 接收者后面，那批调用点不在它的扫描面内；
且它只看调用图可达，从不看 `storage`/`user_id` 的值。`test_distill_usage_accounting`
把出口整个换成 `list.append`，用**不带身份**的 Distiller 跑，断言的是「出口被触发、
action 对」—— 出口在参数为空时照样被调用，只是自己 early-return。

**本锁补的那一维**：身份**值**是否到位、是否落在库里。分五层，各锁一维：

  1. 派生面传播 —— `ctx_thread` / `ctx_submit` 派生出去之后还读不读得到（各一条）；
  2. 写口唯一 —— 全仓 AST 现算：`LLM_CALLER.set` 的调用点只有两处，都在既定出口上
     （`AuthMiddleware.dispatch` 与 `core.request_context.system_llm_context`）；
  3. **身份 ContextVar 唯一** —— 全仓 AST 现算：`ContextVar(...)` 的定义只有两处
     （`LLM_CALLER` 与 `_EMBED_DEADLINE`）。**这一条挡的是「又建一份身份」** ——
     第 2 条只管「谁写」，管不了「写进第几个变量」；
  4. 形态 —— 不再有 `self._user_id` 实例属性；`web/` 里不对**任何**实例写
     `_storage` / `_user_id`（判据按属性名认，不按接收者名字离散）；
  5. 落库（蒸馏族）—— 真 app + 真 `AuthMiddleware` + 真 `/start` 路由，断言
     `usage_stats` 行数 == 出口发出笔数，且 `user_id` 是请求身份；
  6. 聊天族（缺陷 83/84）—— 记账出口签名里没有身份参数、`storage` 在
     `ChatEngine` / `ContextEngine` 上是必填仅关键字、`ContextEngine` 不收
     `usage_ctx` 回调、聊天族三个文件没有 `self._user_id` 且 `self._storage` 只在
     `__init__` 写；
  7. 落库（聊天族）—— 真 app + 真中间件 + 带认证的 SSE `/api/chat/send`，断言
     `usage_stats` 那行的 `action='chat'`、`user_id` 是请求身份。

**身份只有一份。** 读口是 `core.request_context.current_user_id()`，它读的就是门用的
那个 `LLM_CALLER` —— **不是**第二个 ContextVar。这条是本案返工的由来：第一版在
`core/` 另建了一个只装 user_id 的 ContextVar，中间件于是同一个出口连写两份同一个
值。`web/` 没有 `__init__.py`，两个同名 ContextVar 会各看各的，这份「重复」迟早
分叉成两份事实。

**已登记的边界（不是遗漏）。** 第 2 条的扫描面是 `core/` + `web/` + `adapters/`，
**不含 `tests/`**：用例本身要设上下文来观察传播，那是观察装置不是写口。第 5 条只
覆盖 MapReduce 那条分支（短路成单次 longcontext 会变成 3 笔），也只断言「笔数对得上」
—— token 数值是否精确不在这里，那是 `test_llm_access_gate` 的用量记账面。第 2/3 条是
**形锁**：它们能挡住「又写一处」，但挡不住「把身份改从参数传」这类整体换形 —— 那种
变更会先打红第 4 条。第 7 条当时**测不到**「身份读在出口入口而非 `_write` 里」那一维，
理由与实测见该节标题下的登记。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
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
from core.distiller import IDENTIFY_SYSTEM_PROMPT

# WP7 复用：从格式化系统提示词认组别 + 按组回字段齐备的 JSON（不另抄字段样例）
from test_distiller_routing import _format_group_of, _group_reply

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


# ── 3. 身份 ContextVar 唯一 ─────────────────────────────────────────────────


def _contextvar_defs(paths: list[Path]) -> list[str]:
    """全仓现算：`ContextVar(...)` 的**定义**点，返回 `file:line 目标名`。

    判据落在 Call 上（裸 `ContextVar(...)` 与 `contextvars.ContextVar(...)` 都认），
    名字事后按行号回填 —— 只扫赋值语句会漏掉「定义在别处、只是没用赋值接住」的形态。
    查不出名字的记 `<匿名>`，**照样是一次命中**：判断的是「这里新建了一个上下文变量」，
    名字只用来读。
    """
    hits: list[str] = []
    for p in paths:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names: dict[int, str] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Call):
                names[n.value.lineno] = ast.unparse(n.target)
            elif isinstance(n, ast.Assign) and len(n.targets) == 1 \
                    and isinstance(n.value, ast.Call):
                names[n.value.lineno] = ast.unparse(n.targets[0])
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if (isinstance(f, ast.Name) and f.id == "ContextVar") \
                    or (isinstance(f, ast.Attribute) and f.attr == "ContextVar"):
                rel = p.relative_to(_REPO).as_posix()
                hits.append(f"{rel}:{n.lineno} {names.get(n.lineno, '<匿名>')}")
    return sorted(hits)


def _contextvar_def(path: Path, name: str) -> str:
    """某一个具名 ContextVar 的 `file:line 名字` —— 白名单按现算取，不手抄行号。"""
    for h in _contextvar_defs([path]):
        if h.endswith(" " + name):
            return h
    raise AssertionError(f"{path.name} 里没有名为 {name} 的 ContextVar 定义 —— 机制没了")


def test_only_one_identity_contextvar_exists():
    """全仓上下文变量只有三个：身份一个、嵌入超时一个、当前时区一个。

    **白名单按 `file:line 名字` 整串钉死，不按名字**：按名字放行的话，在另一个模块里
    再写一遍 `LLM_CALLER = ContextVar("llm_caller")` 也能过 —— 而那正是要挡的形态
    （`web/` 没有 `__init__.py`，同名两个变量各看各的，中间件设的值另一边读不到）。
    新增一条 ContextVar 时，**先回答它是不是第二份身份**，再来改这里。

    `_current_timezone`（缺陷 96）的答案：**不是**。它装的是「本次请求该用哪个时区」，
    不是「谁在调」—— 判断身份的问题它一个都答不上，读口也只有一个（`UserClock`），
    写口与 `LLM_CALLER` 同在 `AuthMiddleware.dispatch` 那一个出口。多一条时区不会让
    「谁在调」多出第二个答案，这正是本条要挡的东西。
    """
    hits = _contextvar_defs(_py_files(_REPO / "core", _REPO / "web", _REPO / "adapters"))
    allowed = sorted([
        _contextvar_def(_REPO / "core" / "clock.py", "_current_timezone"),
        _contextvar_def(_REPO / "core" / "embeddings.py", "_EMBED_DEADLINE"),
        _contextvar_def(_REPO / "core" / "request_context.py", "LLM_CALLER"),
    ])
    assert hits == allowed, (
        f"上下文变量不止这两个：{hits}，白名单是 {allowed}。带着用户身份的那种只能有 "
        "`LLM_CALLER` 一份 —— 多一份就意味着有两处「谁在调」的答案，中间件只写得进其中一个。")


def test_contextvar_scan_is_not_vacuous():
    """负控：扫描器对**合成**出来的第三个定义必须报得出来 —— 否则上一条是在假绿。"""
    probe = _REPO / "e2e" / "scratch" / "_synthetic_ctxvar_probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        'import contextvars\nX = contextvars.ContextVar("x")\n', encoding="utf-8")
    try:
        hits = _contextvar_defs([probe])
    finally:
        probe.unlink(missing_ok=True)
    assert len(hits) == 1 and hits[0].endswith(" X"), (
        f"扫描器漏掉了合成的 ContextVar 定义（{hits}）—— 判据面失效")


# ── 4. 形态 ────────────────────────────────────────────────────────────────


def test_distiller_holds_no_user_identity_attribute():
    tree = ast.parse((_REPO / "core" / "distiller.py").read_text(encoding="utf-8"))
    found = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "_user_id"
             and isinstance(n.value, ast.Name) and n.value.id == "self"]
    assert not found, (
        f"core/distiller.py 第 {found} 行又出现了 self._user_id —— 身份一旦挂回实例属性，"
        "「谁忘了注入谁就静默不记」这个形态就回来了。身份走 core.request_context 的上下文。")


def test_no_router_writes_deps_onto_instances():
    """`web/` 里不得对**任何**实例写 `_storage` / `_user_id`。

    判据是按**属性名**认的，不按接收者名字离散 —— 这条原来只扫 distiller，挡不住
    聊天族：路由构造完 ChatEngine 之后再往上写属性，写漏一处（写了 `_storage` 忘
    `_user_id`，或反过来）就是整条路静默不记账（缺陷 83）。`X._storage = ` 不论
    `X` 叫什么，都是同一形态的复发。
    """
    offenders: list[str] = []
    for p in _py_files(_REPO / "web"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(n, (ast.Assign, ast.AnnAssign)):
                continue
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr in ("_user_id", "_storage") \
                        and isinstance(t.value, ast.Name):
                    offenders.append(f"{p.relative_to(_REPO).as_posix()}:{t.lineno}")
    assert not offenders, (
        f"路由又往实例上写依赖/身份：{offenders}。storage 由构造注入"
        "（`ChatEngine(..., storage=...)` / `web/deps.get_distiller`），身份由上下文带 "
        "—— 构造后写属性那条路已删，回来就是缺陷 83。")


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
        # WP7：格式化按 4 组并行，每组各要各的字段 —— 按组别分派（组别从系统提示词认），
        # 归并那一跳照旧吐草稿。**不按 user 文案筛**：文案不是接口，改一个字就假绿。
        group = _format_group_of(system)
        text = _group_reply(group) if group else "一份档案草稿。" * 4
        for i in range(0, len(text), 40):
            yield text[i:i + 40]
        return {"prompt_tokens": 13, "completion_tokens": 9, "estimated": False}

    # 长输出入口在生产里是 chat_stream 的薄委托（只放宽读超时）：桩共用同一份记录
    chat_stream_long = chat_stream

    def _make_async_client(self):
        return _FakeClient()

    async def async_chat(self, system, messages, client=None):
        usage = {"prompt_tokens": 21, "completion_tokens": 5}
        # 识别 Map 原样送的就是这个常量（`_identify_over_chunks` 的 `_build_prompt` 直接
        # `return IDENTIFY_SYSTEM_PROMPT, chunk`）。**不能按「识别」二字筛** —— 那个词在
        # 提示词里根本不存在（只在 `IDENTIFY_MERGE_PROMPT` 里，而合并走 chat_stream）。
        if system == IDENTIFY_SYSTEM_PROMPT:
            # 合法**空**名单（不是空串、不是失败）：分片全返回 `[]` → `parts` 为空 →
            # 走 `_identify_over_chunks` 新增的 `if not parts: return []`，不合并、不抛。
            # 于是「点名蒸馏 + 这本书没有具名角色」照常蒸馏下去，identify 恰好记 1 行；
            # 若这里换成非空名单，合并会再记 1 行 → 全流程 10 行，与 expected=9 不符。
            return "[]", usage
        return "甲很沉默，说了一句话。", usage


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
        "id": uid, "username": "t", "role": "user",
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
            # WP7 起格式化的**笔数**变了（1 → 4，按字段组并行），故期望值从 5 改 9；
            # `_wait_for_rows` 的 expected 必须给足 9 —— 给 5 会在跑到一半时快照返回，
            # 下面那条 == 断言就成了竞态。
            rows = _wait_for_rows(str(db), expected=9)
    finally:
        C.ctx_thread = real_ctx_thread
        _IDENTIFY_CACHE.clear()
        try:                        # 同上：SQLiteStore 还攥着句柄，Windows 上删不掉
            db.unlink()
        except OSError:
            pass

    actions = [a for a, _ in rows]
    assert len(rows) == 9, f"落库 {len(rows)} 行（{actions}）—— 出口发出 9 笔却只落这么些"
    assert all(u == uid for _, u in rows), f"落库归属不是请求身份：{rows}"
    assert sorted(actions) == sorted([
        "distill_identify", "distill_map", "distill_reduce",
        "distill_format", "distill_format", "distill_format", "distill_format",
        "distill_autotag", "chat_awakening",
    ]), f"落库的 action 面不对：{actions}"


def _wait_for_rows(db: str, expected: int, timeout: float = 40.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = _usage_rows(db)
        if len(rows) >= expected:
            return rows
        time.sleep(0.25)
    return _usage_rows(db)


# ── 6. 聊天族：storage 是构造依赖，身份由出口自读（缺陷 83/84）──────────────
#
# 上一节锁的是蒸馏族那条路。这一节锁聊天族补上的那一维：**构造后写属性**这条路被
# 删了。原来的形态是「路由构造完引擎，再 `engine._storage = ...`」—— 没有类型看得
# 出来，写漏一处就整条路静默不记账（缺陷 83）；`AgentLoop` 还从 ChatEngine 实例字段
# 上读归属（缺陷 84），而那个字段本身就没人在构造期填。
#
# 两条各锁一维：签名（依赖必填、身份不进签名、没有回调式归属）与实例形态（没有身份
# 属性、storage 只在 `__init__` 写）。「值真的到位」那一维由下一节的行为锁承担 ——
# 记不进去是静默的，形锁本身不会响。

_CHAT_FAMILY = ("core/chat_engine.py", "core/context_engine.py", "core/agent/agent_loop.py")


def test_usage_exit_takes_no_identity_argument():
    """`try_record_usage` 的签名里没有身份 —— 身份归它自己读，不是调用方喂的。

    留一个 `user_id` 形参，就等于把「谁在调」的答案又摊回 13 个调用点：那些点各有
    各的上下文，写漏一处是静默丢账，不是报错。
    """
    from core.utils import try_record_usage

    params = inspect.signature(try_record_usage).parameters
    identityish = [n for n in params if "user" in n]
    assert not identityish, (
        f"记账出口又收身份参数 {identityish}（签名 {list(params)}）—— 归属只应来自 "
        "`core.request_context.current_user_id()`，调用方不再逐处喂。")
    assert "storage" in params, "storage 仍是出口的入参（它是依赖；身份才是被删的那个）"


def test_chat_family_requires_injected_storage():
    """`storage` 在聊天族构造器上是**必填的仅关键字参数**，没有默认值。

    有默认值就有「不传也能构造出来」的实例 —— 那些实例的账是静默丢的（出口那句
    print 只进日志）。缺省 None 是给 `AgentLoop` 这类内部组件的过渡口，不是给引擎的。
    """
    from core.chat_engine import ChatEngine
    from core.context_engine import ContextEngine

    for cls in (ChatEngine, ContextEngine):
        p = inspect.signature(cls.__init__).parameters.get("storage")
        assert p is not None, f"{cls.__name__} 没有 storage 形参 —— 依赖没走构造注入"
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{cls.__name__}.storage 不是仅关键字 —— 位置传参会把 llm/rag 的顺序依赖"
            "又借回来，改签名时静默错位")
        assert p.default is inspect.Parameter.empty, (
            f"{cls.__name__}.storage 有默认值 {p.default!r} —— 又能构造出没有 storage "
            "的实例了，缺陷 83 的静默丢账面跟着回来")


def test_context_engine_takes_storage_not_a_usage_ctx_callback():
    """ContextEngine 直接收 storage，不再收一个「把归属延后到调用时」的回调。

    `usage_ctx=lambda: (self._storage, self._user_id)` 那种形态把归属拆成两半：回调
    返回什么由**持有引擎的那个对象**决定 —— 于是「谁在调」又挂回了实例字段（缺陷 84）。
    """
    from core.context_engine import ContextEngine

    params = inspect.signature(ContextEngine.__init__).parameters
    assert "usage_ctx" not in params, (
        "ContextEngine 又收 usage_ctx 回调了 —— 归属被推迟到调用时、由持有者的实例"
        "字段回答，正是缺陷 84 的形态。它只该接 storage。")


def _instance_write_leaks(rels: tuple[str, ...] = _CHAT_FAMILY) -> list[str]:
    """给定文件里「身份挂回实例」与「storage 写在 `__init__` 之外」的写点。"""
    leaks: list[str] = []
    for rel in rels:
        p = _REPO / rel
        tree = ast.parse(p.read_text(encoding="utf-8"))
        inits = [(n.lineno, n.end_lineno) for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__init__"]
        for n in ast.walk(tree):
            if not isinstance(n, (ast.Assign, ast.AnnAssign)):
                continue
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if not (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self"):
                    continue
                if t.attr == "_user_id":
                    leaks.append(f"{rel}:{t.lineno} self._user_id")
                elif t.attr == "_storage" and not any(a <= t.lineno <= b for a, b in inits):
                    leaks.append(f"{rel}:{t.lineno} self._storage 写在 __init__ 之外")
    return sorted(leaks)


def test_chat_family_holds_no_identity_attribute():
    """聊天族不得再有 `self._user_id`，`self._storage` 只在 `__init__` 里写。

    这两条是同一个形态的两半：身份一旦挂回实例，就又有「谁忘了注入谁就静默不记」；
    storage 一旦能在构造后被改写，路由就又有了「写一半」的余地（缺陷 83）。
    """
    leaks = _instance_write_leaks()
    assert not leaks, (
        f"聊天族又出现构造后写属性/身份挂实例：{leaks}。storage 走必填构造注入，"
        "归属由记账出口读 `core.request_context` —— 实例上不该有这两个字段的写点。")


def test_chat_family_write_scan_is_not_vacuous():
    """负控：扫描器对**合成**的两种写点都要报得出来 —— 否则上一条是在假绿。"""
    probe = _REPO / "e2e" / "scratch" / "_synthetic_chat_family_probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "class E:\n"
        "    def __init__(self, storage):\n"
        "        self._storage = storage\n"
        "    def late_bind(self, storage):\n"
        "        self._storage = storage\n"
        "        self._user_id = 'u'\n",
        encoding="utf-8")
    try:
        leaks = _instance_write_leaks(("e2e/scratch/_synthetic_chat_family_probe.py",))
    finally:
        probe.unlink(missing_ok=True)
    assert len(leaks) == 2 and any("_user_id" in x for x in leaks), (
        f"扫描器漏掉了合成写点（{leaks}）—— 判据面失效")


# ── 7. 落库：真 SSE 聊天请求，账归属**请求身份** ───────────────────────────
#
# 上面那两条形态锁管的是「没有第二处写口」。这一条从路由那端进、到库那端出，管的是
# **值真的到位** —— 缺陷 83 的两半（身份传得进派生线程、storage 到位）任缺一半，
# 这里都是 0 行而不是报错（记不进去是静默的，所以形态锁之外还要一条行为锁）。
#
# 登记一个**测不到**的边界：`try_record_usage` 把身份读在函数入口、不读在 `_write`
# 里，这一条**没有**锁。实测本仓三种投递都传播调用方上下文（`run_coroutine_threadsafe`、
# 无运行 loop 时的 `asyncio.run`、有运行 loop 时的 `create_task`），所以把读取挪进
# `_write` 也照样读到身份 —— 变异打不红，做了就是一条不具分辨力的假锁。要锁它得现造
# 一个不传播上下文的投递实现，那锁的是假想形态。**留作已知的未锁面**：`_write` 里的
# 读法对今天的投递实现是等价的，那种写法只是对「投递契约不承诺传播」更稳。


class _ChatLLM:
    """聊天路径要的那几个口：模型名、usage、流式产出。"""

    def __init__(self) -> None:
        self._model = "lock-chat-model"
        self.last_usage = {"prompt_tokens": 17, "completion_tokens": 4}

    @property
    def model(self) -> str:
        return self._model

    def chat_stream(self, system, messages, max_tokens=None):
        self.last_usage = {"prompt_tokens": 17, "completion_tokens": 4}
        yield "我"
        yield "在。"


def test_chat_sse_lands_usage_row_with_request_identity(monkeypatch):
    """带认证的 SSE 聊天请求 → `usage_stats` 有一行，`action='chat'`、归属请求身份。

    这条从路由那端进、到库那端出，中间不碰任何实例属性 —— 缺陷 83 的两半（身份传得
    进去、storage 到位）任缺一半，这里都是 0 行而不是报错。
    """
    from core.chat_engine import ChatEngine
    from core.schema import CharacterCard
    from core.text_manager import new_session_entry
    from routers.chat import router as chat_router

    uid = f"u_sse_{uuid.uuid4().hex[:8]}"
    db = _REPO / "e2e" / "scratch" / f"_sse_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(str(db))
    sid = f"s_sse_{uuid.uuid4().hex[:8]}"
    tid = f"txt_{uuid.uuid4().hex[:8]}"

    # rag=None：ContextEngine 对未配置检索是**显式空**（不是失败），而真 RAGEngine
    # 构造要 embedding key —— 这条锁的是身份与记账，不是检索，没必要把 key 拖进来。
    engine = ChatEngine(
        _ChatLLM(),
        None,
        CharacterCard(name="甲", identity="测试"),
        card_id="c_lock",
        storage=store,
        session_id=sid,
        is_new_session=True,
    )
    sessions = deps.get_sessions()
    # 条目形状只从 `new_session_entry` 拿（不手搓 dict），也不在这里补字段 ——
    # 在调用点补等于把「条目长什么样」又拆成两处定义。
    sessions[sid] = new_session_entry(engine, None, uid)

    async def _resolve(user_id, storage=None):
        return llm

    llm = engine.llm
    monkeypatch.setattr(deps, "_storage", store)      # 中间件查 users 行要用
    monkeypatch.setattr(deps, "get_user_llm", _resolve)   # `/send` 的 503 门（解析出口）
    app = FastAPI()
    app.include_router(chat_router)
    app.add_middleware(server.AuthMiddleware)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "t", "role": "user",
    }

    try:
        with _LoopSubmitter() as sub:
            # 会话那条链得真在库里：`messages.session_id` 有外键，缺了会话行时
            # `save_message` 抛的是 FK 错、被路由吞成 non-fatal，随后那条路自己会
            # 撞 `user_rec` 未绑定（预存缺陷，不在本次范围）—— 那会让这条锁测到
            # 别的东西上。种子只补链，不碰被测行为。
            sub.run(store.create_user(uid, uid, "probe-hash"))
            sub.run(store.save_text(tid, "src.txt", "甲说了一句话。", user_id=uid))
            sub.run(store.save_card("c_lock", tid, "甲", "{}", user_id=uid))
            sub.run(store.save_session(sid, "c_lock", "", "", uid))
            r = TestClient(app).post(
                "/api/chat/send",
                json={"session_id": sid, "message": "你好", "stream": True},
                headers={"Authorization": f"Bearer {_token(uid)}"},
            )
            rows = _wait_for_rows(str(db), expected=1)
    finally:
        sessions.pop(sid, None)
        try:
            db.unlink()
        except OSError:
            pass

    assert r.status_code == 200, f"SSE 聊天没跑起来：{r.status_code} {r.text[:300]}"
    assert '"done": true' in r.text, f"流没走到终态：{r.text[:300]}"
    assert rows == [("chat", uid)], (
        f"usage_stats 是 {rows}（期望 [('chat', {uid!r})]）—— 走了整条 SSE 路却"
        "没落账，或归属不是请求身份。")
