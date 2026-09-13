# -*- coding: utf-8 -*-
"""边界锁：sqlite store 的每一次连接都必须经 `_ConnectionContext` —— 提交语义的唯一出口。

为什么需要这条锁：`aiosqlite.connect()` 默认 legacy 事务模式（`isolation_level=''`），
INSERT / UPDATE / DELETE 不 commit 就不落盘、close 时被回滚。提交责任已收在
`_ConnectionContext.__aexit__`（成功 commit / 异常 rollback，见 `storage/sqlite_store.py`
的类 docstring）。**绕过它自己开连接 = 绕过了提交保障**：写下去了、函数照常返回成功、
数据不在，而且没有任何报警 —— AGENTS.md 缺陷 24 的第八次同族显形就是这么长出来的
（`add_post_comment` 漏 commit，前端把评论显示出来、刷新即消失）。

这条锁把「绕过」变成可检测的**调用形态**问题，与 `tests/test_storage_scope_lock.py` 同形
（那里锁「读取绕过属主过滤」，这里锁「连接绕过提交出口」）。

白名单只有两个函数，各有理由（不是「偷偷跳过」）：

  - `_connect`：连接工厂，`_ConnectionContext` 的唯一构造点，提交语义在这里成形。
  - `_ensure_initialized`：迁移/建库期，跑在 `_connect` 之前（`_connect` 首行就 await 它），
    自己管连接生命周期；每一步迁移/重建/去重都显式 commit（`_apply_migration` 尾部等），
    且它建出来的表由 `tests/test_sqlite_fresh_schema.py::TestExemptionClosedLoop` **跨连接**
    读回 —— 即已有独立的持久化断言，不靠本锁。

**判据红线**：判据是「有没有绕过出口」，不是「有没有写 commit」—— 在新机制下显式 commit
成了幂等兜底（`delete_user` 等 27 个多步写方法仍保留），拿「必须有 commit」当判据会把
正确写法判红，且那正是本锁要取代的旧口径。
"""
from __future__ import annotations

import ast
import pathlib
import warnings

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SQLITE_STORE = REPO_ROOT / "storage" / "sqlite_store.py"

# 允许直接开连接的函数 —— 键是函数名，值是理由。新增条目必须写明为什么它不能走 _connect。
CONNECT_ALLOWLIST = {
    "_connect": "连接工厂：`_ConnectionContext` 的唯一构造点，提交语义在这里成形",
    "_ensure_initialized": "建库/迁移期，跑在 _connect 之前；每步显式 commit，持久性由 "
                           "TestExemptionClosedLoop 跨连接验证",
}

# 匹配 `aiosqlite.connect(...)` / `sqlite3.connect(...)` —— 直接开连接的两个入口
_CONNECT_TARGETS = ("aiosqlite.connect", "sqlite3.connect")


def _is_direct_connect(node: ast.AST) -> bool:
    """`aiosqlite.connect(...)` / `sqlite3.connect(...)` 这类调用。"""
    return isinstance(node, ast.Call) and ast.unparse(node.func) in _CONNECT_TARGETS


def _direct_connect_functions(tree: ast.AST) -> set[str]:
    """返回直接开连接的函数名（模块级代码归到 `<module>`）。"""
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    out: set[str] = set()
    for node in ast.walk(tree):
        if not _is_direct_connect(node):
            continue
        cur = parents.get(node)
        name = "<module>"
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = cur.name
                break
            cur = parents.get(cur)
        out.add(name)
    return out


def _parse(path: pathlib.Path) -> ast.AST:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(path.read_text(encoding="utf-8"))


def _tree() -> ast.AST:
    return _parse(SQLITE_STORE)


class TestNoConnectOutsideTheOutlet:
    """形态锁：不得绕过 `_ConnectionContext` 自己开连接。"""

    def test_lock_has_teeth(self):
        """负控：探针必须真能看见它要拦的形态，否则「0 处」只是它瞎了。"""
        tree = ast.parse(
            "async def f():\n"
            "    conn = await aiosqlite.connect('x.db')\n"
            "async def g():\n"
            "    async with aiosqlite.connect('y.db') as c:\n"
            "        await c.execute('SELECT 1')\n"
        )
        assert _direct_connect_functions(tree) == {"f", "g"}, "探针认不出直接开连接的形态"

    def test_no_function_bypasses_the_outlet(self):
        unexpected = _direct_connect_functions(_tree()) - set(CONNECT_ALLOWLIST)
        assert not unexpected, (
            f"这些函数绕过了 _ConnectionContext 自己开连接：{sorted(unexpected)}。"
            "自己开的连接没有提交保障 —— aiosqlite 默认 legacy 事务模式，写不 commit 不落盘，"
            "函数却会照常返回成功（缺陷 24：写了、返回成功、数据不在、无告警）。"
            "请改用 `async with await self._connect() as conn:`，提交由 "
            "`_ConnectionContext.__aexit__` 负责；确实必须在 _connect 之前建库/迁移，"
            "才加进 CONNECT_ALLOWLIST 并写明理由。"
        )

    def test_allowlist_has_no_stale_entries(self):
        """反过来：白名单条目若已不再直接开连接，就该删掉，别让名单腐烂。"""
        stale = set(CONNECT_ALLOWLIST) - _direct_connect_functions(_tree())
        assert not stale, f"CONNECT_ALLOWLIST 里的条目已不再命中，请删除：{sorted(stale)}"
