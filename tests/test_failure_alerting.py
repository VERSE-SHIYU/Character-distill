# -*- coding: utf-8 -*-
"""线上失败要能告警（spec-119）：全局处理器 + 吞错落点的级别口径。

**为什么单独一个文件。** 本份改的是「失败可见」这条链上的两处：往上抛的错误由
`web/server.py` 的全局处理器统一记录，被吞掉的错误由各落点自己记录。两者的判据
都要**装真的告警出口**才判得准 —— 面板（`RingBufferHandler`）只收 WARNING+，
告警邮件只收 ERROR，于是「记没记」「记成哪一档」必须分开断言：只断言「有日志」
的话，把 ERROR 写成 WARNING 照样绿，而那一档之差就是「发不发邮件」。

**仪器纪律**（与 test_alerting 同一套）：`_dispatch` 与 `send_email` 都替换在
`core.alerting` 模块自身的名字上 —— 那是生产调用的同一个绑定；只 patch 一处绑定，
绕开它的变异会假绿。

Run: pytest tests/test_failure_alerting.py -v
"""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path

import pytest

import core.alerting as alerting
from core.nonfatal import nonfatal


@pytest.fixture
def alerts(monkeypatch) -> list[tuple[str, str, str]]:
    """把告警出口装成生产那一个（真 `AlertHandler`），发信换成记录式替身。

    装真 handler 而不是手搓一条 `if level >= ERROR` —— 后者测的是本文件里的复制品，
    生产那档门槛（`ALERT_LEVEL`）改了也照绿。

    先把 root 上**已存在**的 AlertHandler 摘下来（`install_alert_handler` 是进程级装配，
    别的用例可能已经装过）：留着它同一封会被两个 handler 各发一次，「发了几封」就不再
    是本用例的事实。
    """
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(alerting, "_dispatch", lambda fn: fn())
    monkeypatch.setattr(
        alerting, "send_email",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    root = logging.getLogger()
    existing = [h for h in root.handlers if isinstance(h, alerting.AlertHandler)]
    for h in existing:
        root.removeHandler(h)
    handler = alerting.AlertHandler("ops@example.com")
    root.addHandler(handler)
    try:
        yield sent
    finally:
        root.removeHandler(handler)
        for h in existing:
            root.addHandler(h)


def _levels_for(caplog, marker: str) -> list[int]:
    """本次调用产生的、带 marker 的记录级别（marker 是本用例独有的，不假设空缓冲区）。"""
    return [r.levelno for r in caplog.records if marker in r.getMessage()]


# ── 1. `nonfatal` 的级别：默认 ERROR（会告警），可选 WARNING（只进面板） ──────

def test_nonfatal_level_warning_is_recorded_but_sends_no_alert(alerts, caplog):
    """`level=WARNING` → 记 WARNING，且**不**发告警邮件。

    这一档就是「已经兜底了、不影响结果」的后台动作（好感度、阅读进度、预热、缓存）：
    它们该上面板，但不该把人从床上叫起来。写成 ERROR 的代价是真会发信。
    """
    marker = "spec119-nonfatal-warning"

    async def _run() -> None:
        async with nonfatal("probe", marker, level=logging.WARNING):
            raise RuntimeError("warn-boom")

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run())

    assert _levels_for(caplog, marker) == [logging.WARNING], \
        f"应恰有一条 WARNING（吞掉 ≠ 沉默），实得 {_levels_for(caplog, marker)}"
    assert alerts == [], "WARNING 记成了告警 —— 兜底动作失败会开始发邮件"


def test_nonfatal_default_level_is_unchanged(alerts, caplog):
    """不传 `level` → 仍是 ERROR 且**触发告警**（现有 9 处调用行为不变）。

    「默认值不变」不能只靠「现有用例还绿」来说：那些用例断言的是面板上有记录，
    而面板收 WARNING+，把默认降成 WARNING 它们照样绿。
    """
    marker = "spec119-nonfatal-default"

    async def _run() -> None:
        async with nonfatal("probe", marker):
            raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        asyncio.run(_run())

    assert _levels_for(caplog, marker) == [logging.ERROR], \
        f"不传 level 时必须是 ERROR，实得 {_levels_for(caplog, marker)}"
    assert len(alerts) == 1, "默认档（数据没存进去 / 请求失败）必须发得出告警"


# ── 2. 全局异常处理器：往上抛的错误必须留痕（并触发告警） ─────────────────────
#
# 这一处是「约 300 处打印后抛出」的统一接住点：那些 print 已经足够定位，不值得逐处改，
# 但它们**也只落在 stdout 里**。全局处理器记一条，整条路才上面板、才会发信。
#
# 用**生产 app 本身**（不是另搭一个最小 app）：处理器是 `web/server.py` 里注册的那一个，
# 另搭一个等于把「生产这一份装配真的生效了」这件事测成「我照着又注册了一遍」。

@pytest.fixture
def probe_app():
    """在生产 app 上临时挂一条会抛的路由；用完摘掉。

    「摘掉」不是洁癖：仓里有若干**枚举路由**的锁（route_facts 的对账、演示门禁的射程），
    多出来的路由会让它们按另一份现场判事。`openapi_schema` 缓存一并清 —— 那个缓存按
    首次求值冻结，带着探针路由的 schema 会留给后面的用例。
    """
    from starlette.routing import Route

    import server

    async def _boom(request):
        raise RuntimeError("spec119-boom")

    route = Route("/__spec119_boom__", _boom, methods=["GET"])
    server.app.router.routes.insert(0, route)
    try:
        yield server.app
    finally:
        server.app.router.routes.remove(route)
        server.app.openapi_schema = None


def test_unhandled_exception_is_logged_and_alerts_while_response_is_unchanged(
    probe_app, alerts, caplog,
):
    """未捕获的异常 → 500、响应**一字不改**、一条带堆栈的 ERROR、告警 1 封。

    「响应不变」与「必须留痕」是同一处的两条相反约束：这一改动的诱惑正是顺手把异常
    原文端给前端（那既改了契约，又把内部细节漏出去）。所以两者钉在一起。
    """
    from fastapi.testclient import TestClient

    client = TestClient(probe_app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/__spec119_boom__")

    assert (resp.status_code, resp.json()) == (
        500, {"detail": "服务器内部错误，请稍后重试"},
    ), f"返回给前端的响应变了：{resp.status_code} {resp.text!r}"

    recs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [(r.levelno, bool(r.exc_info)) for r in recs] == [(logging.ERROR, True)], \
        f"未捕获的异常必须留一条带堆栈的 ERROR，实得 {[(r.levelno, bool(r.exc_info)) for r in recs]}"
    assert "GET" in recs[0].getMessage() and "/__spec119_boom__" in recs[0].getMessage(), \
        f"日志没说是哪个请求挂的，排障只剩堆栈：{recs[0].getMessage()!r}"

    assert len(alerts) == 1, "线上 500 却没发告警 —— 这正是本份要修的事"


# ── 3. 线上不再有「打印后吞掉」的失败（spec-119 残留：判据改成按结构判定）──────────
#
# 这一类就是本份要消灭的形态：错误既不抛给全局处理器（没人接住它），也不进 logging
# —— 面板（收 WARNING+）与告警邮件（收 ERROR）都看不见它。
#
# 判据（不再只按 `fail` 这个词判），两条都满足才算违规：
#   (a) 这个 `print` 落在某个 `except` 分支里（向上找最近的 `ExceptHandler`，碰到函数或
#       类定义就停），**或者**它的源码里含 `fail`（保留文本规则，覆盖不在 `except` 里、
#       按结果判失败的情况）；
#   (b) 从它往后执行下去，**不必然**把失败交给日志。
#
# `_always_raises` 两处就地修正（不新造机制）：
#   - 普通语句（赋值、表达式、`with` 的块体等）算「继续往下走」，`return` / `break` /
#     `continue` 算「不抛出」；于是 `print → history.pop() → raise` 能正确判成必然抛出
#     （旧判据只认紧邻的 `raise`，中间隔一条普通语句就误判成吞错）；
#   - `raise HTTPException(...)` **不算**交给日志 —— 它走不到全局处理器，失败同样只落
#     在容器 stdout（旧判据把它当「必然抛出」放过）。
#
# 找语句所在的列表要遍历父节点**所有**列表字段（`body` / `orelse` / `finalbody` /
# `handlers` 下的 body），不再只看 `.body`：旧写法在 `else` / `finally` / `handlers`
# 里的语句上会静默 `continue`，整类漏判。
#
# 豁免只有一处，登记在 `_KEPT_PRINTS`，理由随条目走。结果按 **(路径, 函数名)** 收集成
# 列表，与 `sorted(_KEPT_PRINTS)` 做**相等**比较：一个比较同时拦住新增违规和豁免失效
# （豁免那处被改好、别处漏进 print，两边都会变红）。不用行号 —— 行号会漂。

_SCAN_ROOTS = ("storage/postgres_store.py", "web", "core", "adapters")
_REPO = Path(__file__).resolve().parent.parent

_KEPT_PRINTS = {
    ("core/alerting.py", "_main"):
        "告警处理器自检发信失败只能写 stderr：它就在告警链末端，写日志会递归",
}

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _always_raises(stmts: list[ast.stmt]) -> bool:
    """从语句列表开头执行下去，是否**每条路径都必然把失败交给日志**。

    只看列表里的控制流，够了：判据要回答的只是「`print` 之后失败还会不会悄悄回到调用方」。
    `if` 的两个分支各自接上列表余下的语句再递归 —— 空分支代表「落到后面」，于是
    `if c: raise A` + `raise B` 这种也判为必然抛出。普通语句继续往下走（`with` 的块体
    也按「继续」处理）；`return` / `break` / `continue` 是失败就此消失，算不抛出。
    """
    if not stmts:
        return False
    head, rest = stmts[0], stmts[1:]
    if isinstance(head, ast.Raise):
        return not _is_http_exception(head)
    if isinstance(head, (ast.Return, ast.Break, ast.Continue)):
        return False
    if isinstance(head, ast.If):
        body = list(head.body) + rest
        orelse = list(head.orelse) + rest if head.orelse else rest
        return _always_raises(body) and _always_raises(orelse)
    return _always_raises(rest)


def _is_http_exception(node: ast.Raise) -> bool:
    """`raise HTTPException(...)` 进不了全局处理器，失败只落 stdout，不算交给日志。"""
    exc = node.exc
    if not isinstance(exc, ast.Call):
        return False
    func = exc.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return name == "HTTPException"


def _enclosing_scope(node: ast.AST, parent: dict[ast.AST, ast.AST]) -> str:
    """离得最近的函数名（不在任何函数里就是模块级）。"""
    cur = parent.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parent.get(cur)
    return "<module>"


def _in_except_handler(node: ast.AST, parent: dict[ast.AST, ast.AST]) -> bool:
    """向上找最近的 `ExceptHandler`；先碰到函数/类定义就说明不在 except 里。"""
    cur = node
    while cur is not None:
        if isinstance(cur, ast.ExceptHandler):
            return True
        if isinstance(cur, _SCOPE_NODES):
            return False
        cur = parent.get(cur)
    return False


def _print_swallows_in(path: Path) -> list[tuple[str, str]]:
    """本文件里所有吞错 `print` 的 `(相对路径, 所在函数名)` 列表。"""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:            # 扫到语法坏的文件要当场炸，不能静默跳过
        raise AssertionError(f"{path}: 扫描目标无法解析：{exc}") from exc

    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for _field, val in ast.iter_fields(node):
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, ast.AST):
                        parent[item] = node
            elif isinstance(val, ast.AST):
                parent[val] = node

    bad: list[tuple[str, str]] = []
    rel = path.relative_to(_REPO).as_posix()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        if not (_in_except_handler(node, parent)
                or "fail" in (ast.get_source_segment(src, node) or "")):
            continue
        stmt = node
        while stmt is not None and not isinstance(stmt, ast.stmt):
            stmt = parent.get(stmt)
        siblings = next(
            (val for _field, val in ast.iter_fields(parent.get(stmt))
             if isinstance(val, list) and stmt in val),
            None,
        )
        if siblings is None:
            continue
        i = siblings.index(stmt)
        if _always_raises(siblings[i + 1:]):
            continue                       # 打印后必然交给日志 —— §2.2 不动
        bad.append((rel, _enclosing_scope(stmt, parent)))
    return bad


def _scan_files():
    for root in _SCAN_ROOTS:
        p = _REPO / root
        if p.is_file():
            yield p
        else:
            yield from sorted(
                q for q in p.rglob("*.py") if "__pycache__" not in q.parts
            )


def test_no_print_swallowed_failures_left_in_production_code():
    """`postgres_store.py` / `web` / `core` / `adapters` 里的吞错只应剩登记的那一处。"""
    found = [site for p in _scan_files() for site in _print_swallows_in(p)]
    assert sorted(found) == sorted(_KEPT_PRINTS), (
        "这些地方的失败只落在容器 stdout 里，面板与告警都看不见 —— "
        f"改用 nonfatal 或模块 logger：{sorted(found)}"
    )
