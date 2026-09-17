"""取数工具 —— **不是判据，也不要把它做成锁**。

命题：全仓有多少「`__init__` 里赋值给 `self._X`、类体内零读取」的私有属性。
即「构造参数落进属性后，这个类自己从不看它」。

**为什么不建锁**（2026-09-17 实测基准，别重新论证一遍）：命中的形状**分不出真假** ——
`core/embeddings.py` 的 `Mem0BridgeEmbedder._dimensions` 是命中之一，而它被
`core/rag.py` 以 `getattr(self._embedding_function, "_dimensions", None)` 读取，
**那正是 `CollectionUnusableError` 的维度判据**（幂等负控二所依赖的机制）。
`getattr` 形式的读取对任何 AST 形状扫描都是隐形的：没有 `.` 前导，也不在同一个类里。
要排掉它就得挂一张豁免表 —— 那正是「守卫与被守对象之间的第二份手工清单」（§四 ③层）。
故此处只取数：**看红的人自己判每一条是真死还是外部读**。

基准读数（2026-09-18，`main`）：**扫描面 95 个入库 .py / 命中 9 处**。
台账引用的就是这两个数 —— 重跑对不上先查扫描面，别先怀疑判据。

判据（重跑的人照着对）：
  1. **扫描面 = `git ls-files '*.py'` 去掉 `tests/`** —— 只认**入库**文件。
     故 gitignore 的 scratch 不进分母，干净克隆与本地跑出的数因此**一致**。
     （实测：`git ls-files --others --ignored --exclude-standard '*.py'` 去 `tests/`
     /`.venv/`/`services/` 后 105 个文件，只 1 处命中 —— `e2e/scratch_3c_resume.py`
     的 `FakeLLM._make_async_client`。**这批文件全在仓库外**，故不影响上面的 95/9。）
  2. 只认 `ast.Attribute` 且 `value` 是 `Name("self")`、`attr` 以单个 `_` 开头
     （`__dunder__` 排除）；
  3. **写** = 出现在该类 `__init__` 体内的 `Store`；**读** = 类体内任意位置的 `Load`。
     `self._x[k] = v` 里的 `self._x` 是 `Load` —— 「用下标写」算读，不算写
     （`self._sessions[sid] = ...` 确实在用这个字段）；
  4. 有写、零读 → 命中。

用法：`python tests/census_dead_attrs.py` 打印读数；pytest 侧要取数则 `import census_dead_attrs`
（与先例 `tests/route_facts.py` 同款：裸模块名，靠 pytest 把用例目录塞进 sys.path）。
本模块**不被任何用例收集**（文件名不以 `test_` 开头），也不该被断言 —— 它不是锁。
"""
from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Hit:
    path: str
    cls: str
    attr: str


def _tracked_py() -> list[str]:
    """入库的 .py，去掉 tests/ —— 扫描面必须只由 git 决定，不靠手工排除表。"""
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=_REPO,
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return [p for p in out.splitlines() if p and not p.startswith("tests/")]


def _class_attrs(cls: ast.ClassDef) -> tuple[dict[str, int], dict[str, int]]:
    """返回 (写: 仅在 __init__ 内, 读: 类体内任意位置)。"""
    writes: dict[str, int] = {}
    reads: dict[str, int] = {}
    inits = [
        n for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__init__"
    ]
    for node in ast.walk(cls):
        if not isinstance(node, ast.Attribute):
            continue
        if not (isinstance(node.value, ast.Name) and node.value.id == "self"):
            continue
        if not node.attr.startswith("_") or node.attr.startswith("__"):
            continue
        if isinstance(node.ctx, ast.Store):
            continue  # 写由 _init_writes 单独收，避免把方法体内的赋值混进来
        reads[node.attr] = reads.get(node.attr, 0) + 1
    for init in inits:
        for node in ast.walk(init):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr.startswith("_")
                and not node.attr.startswith("__")
            ):
                writes[node.attr] = writes.get(node.attr, 0) + 1
    return writes, reads


def census() -> list[Hit]:
    """返回本命题的全部命中（排序稳定，便于两次读数直接 diff）。"""
    hits: list[Hit] = []
    for rel in _tracked_py():
        try:
            tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            writes, reads = _class_attrs(cls)
            for attr in writes:
                if not reads.get(attr):
                    hits.append(Hit(rel, cls.name, attr))
    return sorted(hits, key=lambda h: (h.path, h.cls, h.attr))


def main() -> None:
    hits = census()
    print(f"扫描 {len(_tracked_py())} 个入库 .py（不含 tests/）")
    print(f"命中 {len(hits)} 处：`__init__` 里赋值、类体内零读取\n")
    for h in hits:
        print(f"  {h.path:<42} {h.cls:<26} {h.attr}")
    print(
        "\n注意 `core/embeddings.py` 的 `Mem0BridgeEmbedder._dimensions` 是**误判**"
        "（被 core/rag.py 用 getattr 外部读取）—— 见模块 docstring。"
    )


if __name__ == "__main__":
    main()
