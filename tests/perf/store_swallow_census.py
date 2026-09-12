"""缺陷 21 步骤 3.1 普查：store 层「把失败吞成空值」的 except 块全集（AST，不是肉眼 grep）。

**为什么要入库**：commit message 里写了「154 处显式 + 20 处隐式 = 174 处 A 类，
7 处 B 类豁免」，这个数字必须能被复算。本脚本支持 `--ref`，所以改完之后仍能
拿历史版本重跑出同一份清单：

    python tests/perf/store_swallow_census.py --ref 4a608f9   # 改之前：174 A + 7 B
    python tests/perf/store_swallow_census.py                 # 改之后：0 A + 7 B

两种形态（同族，必须一起数）：
  - 显式 `except ...: print(...); return <空值>`
  - 隐式 `except ...: print(...)` 且该 try 是函数体最后一句 → 落到 `return None`

`# store-empty-ok:` 标记 = B 类豁免（捕获的异常与「结果为空」无关），不计入 A 类。
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("storage/sqlite_store.py", "storage/postgres_store.py")
EXEMPT_MARKER = "# store-empty-ok:"
EMPTY = frozenset({
    "None", "False", "[]", "{}", "''", '""', "set()", "0", "()",
    "{'cards': [], 'texts': [], 'users': []}", "('', False)", "{'following': False}",
})


def _source(rel: str, ref: str | None) -> str:
    if ref is None:
        return (ROOT / rel).read_text(encoding="utf-8")
    return subprocess.check_output(["git", "show", f"{ref}:{rel}"], cwd=ROOT, text=True, encoding="utf-8")


def census(src: str) -> tuple[list[tuple[int, str, str]], list[tuple[int, str]]]:
    """返回 (A 类 [(行, 方法, 形态)], B 类 [(行, 方法)])。"""
    lines = src.splitlines()
    a: list[tuple[int, str, str]] = []
    b: list[tuple[int, str]] = []

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.fn: list[ast.AST] = []

        def _fn(self, n: ast.AST) -> None:
            self.fn.append(n)
            self.generic_visit(n)
            self.fn.pop()

        visit_AsyncFunctionDef = visit_FunctionDef = _fn

        def visit_Try(self, node: ast.Try) -> None:
            # 隐式形态只在 try 位于函数体终末时才算「落到 None」——
            # 否则 except 后还有代码，函数会继续走到别的 return。
            terminal = bool(self.fn) and getattr(self.fn[-1], "body", [])[-1] is node
            name = self.fn[-1].name if self.fn else "?"  # type: ignore[attr-defined]
            for h in node.handlers:
                if any(isinstance(s, ast.Raise) for s in ast.walk(h)):
                    continue  # 上抛 = 失败可见
                exempt = EXEMPT_MARKER in "\n".join(lines[h.lineno - 1:h.end_lineno])
                returns = [s for s in ast.walk(h) if isinstance(s, ast.Return)]
                shapes = [
                    f"return {ast.unparse(s.value) if s.value is not None else 'None'}"
                    for s in returns
                    if (ast.unparse(s.value) if s.value is not None else "None") in EMPTY
                ]
                if not returns and terminal:
                    shapes.append("隐式 None")
                for shape in shapes:
                    if exempt:
                        b.append((h.lineno, name))
                    else:
                        a.append((h.lineno, name, shape))
            self.generic_visit(node)

    V().visit(ast.parse(src))
    return a, b


def _self_check() -> None:
    """负控 + 正控：探针认得出两种形态、认得出豁免标记。"""
    src = (
        "async def f():\n"
        "    try:\n        pass\n"
        "    except Exception as exc:\n        print(exc)\n        return []\n"
        "async def g():\n"
        "    try:\n        pass\n"
        "    except Exception as exc:\n        print(exc)\n"
        "async def h():\n"
        "    try:\n        pass\n"
        "    except Exception as exc:\n        # store-empty-ok: 演示\n        print(exc)\n"
    )
    a, b = census(src)
    assert [(x[1], x[2]) for x in a] == [("f", "return []"), ("g", "隐式 None")], a
    assert [x[1] for x in b] == ["h"], b
    print("_self_check: 显式 / 隐式 / 豁免标记 三种都认得出 —— OK\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=None, help="git ref（如 4a608f9）；省略则读工作区")
    args = ap.parse_args()
    _self_check()
    ta = tb = 0
    for rel in TARGETS:
        a, b = census(_source(rel, args.ref))
        print(f"=== {rel}（{'工作区' if args.ref is None else args.ref}）")
        print(f"  A 类（失败吞成空值，无豁免）：{len(a)}")
        for lineno, fn, shape in a:
            print(f"    :{lineno}\t{fn}\t{shape}")
        print(f"  B 类（带 store-empty-ok 豁免）：{len(b)}")
        for lineno, fn in b:
            print(f"    :{lineno}\t{fn}")
        ta += len(a)
        tb += len(b)
    print(f"\n合计：A 类 {ta} 处 / B 类 {tb} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
