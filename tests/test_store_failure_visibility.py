"""store 层「失败与空结果不可区分」的收敛锁（缺陷 21，第七个同族形态）。

**被锁的命题**：store 方法的空返回值只表示「无数据」，永不表示「失败」。

立这条的理由：两个 store 里曾有 154 处 `except Exception: print(...); return <空值>`，
另有 20 处 `except Exception: print(...)` 直接落到隐式 `None` —— 两种形态都让
「查到了、结果是空」与「查询失败了」在返回值上不可分辨。第七次显形的路径是
SQLite 新库缺 `remote_user_profiles` 表 → `get_conversations` 把 `OperationalError`
吞成空列表 → 私信收件箱恒为空、不报错、不 500。前六个同族形态（线程弃船 / 384 维度 /
截断响应 / `finish_reason` 缺失 / `$contains` 恒不命中 / `admin_tasks` 静默截断）
都是因为「只修出问题那处」才长出来的，故本轮全量收敛到 `storage.base.StoreError`。

三层防线，各管各的：

1. `TestNoSwallowingHandlers` —— **形态锁**：AST 扫两个 store 文件，任何 `except` 处理块
   把失败吞成空值（显式 `return <空值>` **或** 只 print 后落到隐式 `None`）即红。
   口径内的 B 类豁免 2 条（sqlite `__aexit__` / pg `_parse_rowcount`），必须写
   `# store-empty-ok:` 注释；另有 7 处「吞了但不在本口径内」的同类处理（非终末位置、
   不返回空值，如 `export_session` 的 JSON 降级）也加了同样的标记说明，不计入本锁。
2. `TestFailureIsDistinguishable` —— **动态断言**：把连接换成必然失败的桩，
   遍历每个真正碰库的方法，断言失败被上抛（而非空值 / 静默兜底）。
3. `TestZeroSevenNineRegression` —— **原始缺陷的复现断言**：删表 → 三个引用点报错而非静默。

普查全集与 A/B 分类（含 `--ref` 复算）见 `tests/perf/store_swallow_census.py`。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import sqlite3
import uuid
from pathlib import Path

import pytest

from storage.base import StoreError
from storage.sqlite_store import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
STORE_FILES = (ROOT / "storage" / "sqlite_store.py", ROOT / "storage" / "postgres_store.py")

# 形态锁的豁免标记 —— 只有它能让一个「吞掉失败」的 except 合法通过
EXEMPT_MARKER = "# store-empty-ok:"

# 「空值」= 与「无数据」不可区分的返回值。注意不含 True：查询成功返回 True 是肯定结果。
_EMPTY_SOURCE = frozenset({
    "None", "False", "[]", "{}", "''", '""', "set()", "0", "()",
    "{'cards': [], 'texts': [], 'users': []}", "('', False)", "{'following': False}",
})


def _swallowing_handlers(path: Path) -> list[tuple[int, str]]:
    """返回 (行号, 形态) —— 命中「except 块吞掉失败且无豁免标记」。

    两种形态同族，必须一起锁：
      - 显式：`except ...: print(...); return <空值>`
      - 隐式：`except ...: print(...)` 且该 try 是函数体最后一句 → 落到 `return None`
    """
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    hits: list[tuple[int, str]] = []

    def enclosing_last_stmt(fn: ast.AST) -> ast.AST | None:
        for field in ("body",):
            body = getattr(fn, field, None)
            if isinstance(body, list) and body:
                return body[-1]
        return None

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.fn_stack: list[ast.AST] = []

        def _visit_fn(self, node: ast.AST) -> None:
            self.fn_stack.append(node)
            self.generic_visit(node)
            self.fn_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef = _visit_fn

        def visit_Try(self, node: ast.Try) -> None:
            terminal = bool(self.fn_stack) and enclosing_last_stmt(self.fn_stack[-1]) is node
            for h in node.handlers:
                if EXEMPT_MARKER in "\n".join(lines[h.lineno - 1:h.end_lineno]):
                    continue
                if any(isinstance(s, ast.Raise) for s in ast.walk(h)):
                    continue  # 上抛 = 失败可见，不是吞
                returns = [s for s in ast.walk(h) if isinstance(s, ast.Return)]
                if returns:
                    for stmt in returns:
                        rendered = ast.unparse(stmt.value) if stmt.value is not None else "None"
                        if rendered in _EMPTY_SOURCE:
                            hits.append((stmt.lineno, f"return {rendered}"))
                elif terminal:
                    hits.append((h.lineno, "隐式 None（except 只 print，不 return 也不 raise）"))
            self.generic_visit(node)

    V().visit(ast.parse(src))
    return hits


class TestNoSwallowingHandlers:
    """形态锁：`except` 块不得把失败吞成空值（显式空返回 / 隐式落 None 都算）。"""

    def test_lock_has_teeth(self):
        """负控：锁必须真能看见它要拦的两种形态，否则「0 处」只是它瞎了。"""
        explicit = ast.parse("async def f():\n    try:\n        pass\n    except Exception:\n        return []\n")
        implicit = ast.parse("async def f():\n    try:\n        pass\n    except Exception as exc:\n        print(exc)\n")
        for tree, want in ((explicit, "return []"), (implicit, "隐式")):
            found: list[str] = []
            for n in ast.walk(tree):
                if isinstance(n, ast.Try):
                    h = n.handlers[0]
                    found.append("return []" if any(isinstance(s, ast.Return) for s in ast.walk(h)) else "隐式")
            assert want in found, f"探针认不出形态 {want}"

    @pytest.mark.parametrize("path", STORE_FILES, ids=lambda p: p.name)
    def test_no_handler_swallows_failure(self, path: Path):
        hits = _swallowing_handlers(path)
        assert not hits, (
            f"{path.name} 有 {len(hits)} 处 except 块把失败吞成空值：{hits[:8]}。"
            "store 层的空返回值只能表示「无数据」。失败请走 "
            "`raise StoreError(\"<方法名>\", exc) from exc`（storage/base.py 的单一定义）；"
            "确属「无数据」语义的例外必须就地写 `# store-empty-ok: <理由>` 说明为什么"
            "捕获的异常与「结果为空」无关。")


# 桩的哨兵串：出现在异常链里 = 库失败被**上抛**了（不是参数校验之类的早退崩）
BOOM = "__PROBE_BOOM__"

_REACHED: set[str] = set()
_CURRENT: list[str] = [""]


class _BoomConn:
    """必然失败的连接桩：任何语句都抛 `OperationalError`（模拟缺表 / 断连）。"""

    async def execute(self, *a, **kw):
        raise sqlite3.OperationalError(f"no such table: {BOOM}")

    async def executescript(self, *a, **kw):
        raise sqlite3.OperationalError(f"no such table: {BOOM}")

    async def commit(self):
        raise sqlite3.OperationalError(f"no such table: {BOOM}")

    async def close(self):
        return None

    def __getattr__(self, name):
        raise sqlite3.OperationalError(f"no such table: {BOOM} ({name})")


class _BoomContext:
    """必须能 `await` —— 调用点是 `async with await self._connect() as conn`。"""

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        if _CURRENT[0]:
            _REACHED.add(_CURRENT[0])  # 记下「这个方法真的走到了库」
        return _BoomConn()

    async def __aexit__(self, *exc):
        return False


def _boom(**_kw):
    return _BoomContext()


def _chain_text(exc: BaseException) -> str:
    """展开 `__cause__`/`__context__` 链 —— 取异常链全文本。"""
    parts: list[str] = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        parts.append(f"{type(exc).__name__}: {exc}")
        exc = exc.__cause__ or exc.__context__
    return " | ".join(parts)


def _dummy_args(fn) -> tuple[list, dict]:
    """给方法填一组哑参数，只为走到 `_connect`；值本身不参与断言。

    跳过 `self`；每个参数给**互不相同**的值 —— 否则 `viewer_id == target_id`
    这类早退分支会在碰库前返回，探针把「没碰库」误算成「碰了库却没报错」。

    注解要按**字符串**比：两个 store 都有 `from __future__ import annotations`，
    `p.annotation` 是 `'bool'` 而不是 `bool`，用 `is` 比恒不成立（本探针踩过）。
    """
    args: list = []
    for i, p in enumerate(inspect.signature(fn).parameters.values()):
        if p.name == "self" or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is not p.empty:
            continue
        ann = p.annotation if isinstance(p.annotation, str) else getattr(p.annotation, "__name__", "")
        args.append({"int": 0, "bool": False, "float": 0.0, "str": f"probe_{i}"}.get(ann.strip("'\""), f"probe_{i}"))
    return args, {}


class TestFailureIsDistinguishable:
    """动态回归：全量遍历碰库的方法，失败必须可辨。

    判别口径不是「异常类型是不是 StoreError」—— 本仓另有 302 处
    `except Exception: print(...); raise`（原样上抛）也是失败可见。真正的口径是：
    **库失败有没有消失**。桩抛的异常带哨兵串，只要它出现在上抛异常的链里，
    就说明失败被传出来了；若方法正常返回了一个空值，就是被吞了。

    分母用「真的走到了库的方法数」（`_BoomContext.__aenter__` 记账），不是
    「碰库方法总数」—— 后者里有一批在参数校验阶段就早退（哑参数不合类型），
    拿它当分母会让「探针没走到库」看起来像「方法没吞错」。
    """

    def _probe(self):
        """返回 (返回空值的方法列表, 失败可见的方法集, 走到库的方法集, 探测总数)。"""
        store = SQLiteStore(":memory:")
        returned_empty: list[str] = []
        surfaced: set[str] = set()
        probed = 0
        src = STORE_FILES[0].read_text(encoding="utf-8")
        for name, fn in sorted(vars(SQLiteStore).items()):
            if name.startswith("_") or not inspect.iscoroutinefunction(fn):
                continue
            if f"def {name}(" not in src or "_connect" not in inspect.getsource(fn):
                continue
            probed += 1
            store._connect = _boom  # type: ignore[method-assign]
            args, kwargs = _dummy_args(fn)
            _CURRENT[0] = name
            try:
                result = asyncio.run(getattr(store, name)(*args, **kwargs))
            except Exception as exc:
                if BOOM in _chain_text(exc):
                    surfaced.add(name)
                continue  # 参数校验等非库失败，与「吞成空值」无关
            finally:
                _CURRENT[0] = ""
            rendered = "None" if result is None else repr(result)
            # 只有「真的走到库、拿到库失败、却正常返回空值」才算吞错。
            # 参数校验阶段的早退（如 visibility 不合法 → return False）没碰库，不算。
            if rendered in _EMPTY_SOURCE and name in _REACHED:
                returned_empty.append(f"{name} -> {rendered}")
        return returned_empty, surfaced, probed

    def test_no_method_returns_empty_on_db_failure(self):
        _REACHED.clear()
        returned_empty, surfaced, probed = self._probe()
        assert not returned_empty, (
            f"这些方法在库失败时返回了空值而非把失败抛出来：{returned_empty}")
        # 探针自效性：确认真把库打坏并走到了足够多的方法
        assert probed > 200, f"只探到 {probed} 个方法 —— 过滤条件写歪了"
        assert len(_REACHED) > 100, (
            f"只有 {len(_REACHED)} 个方法真的走到了库 —— 哑参数不合类型，探针空转")
        # 不变量：走到库且拿到库失败的方法，失败必须要么上抛、要么（违规地）返回空值。
        # 两者都漏的（静默返回非空兜底）单独列出，供人审。
        missed = _REACHED - surfaced - {e.split(" -> ")[0] for e in returned_empty}
        assert not missed, f"这些方法库失败后既没上抛、也没返回空值，而是静默兜底：{sorted(missed)}"


class TestZeroSevenNineRegression:
    """原始缺陷的复现断言：表没了，调用方必须能分辨（而不是收到空列表）。

    **必须用临时文件库**：`aiosqlite.connect(":memory:")` 每次 `_connect()` 都是
    一个新连接 → 一个全新的空库，多调用之间不共享状态，删表/建表都留不下来。
    """

    async def _store_without_the_table(self, tmp_path: Path) -> SQLiteStore:
        """用同步 sqlite3 做 DDL —— aiosqlite 连接上 DROP 会撞 `database table is locked`。"""
        db = str(tmp_path / "probe.db")
        store = SQLiteStore(db)
        await store._ensure_initialized()
        raw = sqlite3.connect(db)
        try:
            raw.execute("DROP TABLE remote_user_profiles")
            raw.commit()
        finally:
            raw.close()
        return store

    async def test_conversations_raises_instead_of_silently_empty(self, tmp_path: Path):
        """这**就是**缺陷现场：此前返回 `[]`，收件箱静默为空。"""
        store = await self._store_without_the_table(tmp_path)
        with pytest.raises(StoreError) as ei:
            await store.get_conversations("usr_x")
        assert ei.value.op == "get_conversations"

    async def test_profile_read_and_write_raise(self, tmp_path: Path):
        """这两个引用点此前静默 —— get 吞成 None，upsert 吞成「写成功」。

        断言口径是**失败可辨**，不是固定异常类型：本仓另有 302 处
        `except Exception: print(...); raise`（原样上抛原始异常）同样是失败可见，
        这两个方法就走那条。真被吞掉时这里是「没有异常」而非「异常类型不对」。
        """
        store = await self._store_without_the_table(tmp_path)
        with pytest.raises(Exception, match="no such table: remote_user_profiles"):
            await store.upsert_remote_user_profile("u", "n", "cn", "")
        with pytest.raises(Exception, match="no such table: remote_user_profiles"):
            await store.get_remote_user_profile("u")

    async def test_healthy_db_still_returns_empty_meaning_no_data(self, tmp_path: Path):
        """不变量反向确认：表在、只是没数据 → 仍是正常的空列表，不是异常。"""
        store = SQLiteStore(str(tmp_path / "healthy.db"))
        await store._ensure_initialized()
        assert await store.get_conversations(f"usr_{uuid.uuid4().hex}") == []
