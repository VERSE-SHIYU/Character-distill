# -*- coding: utf-8 -*-
"""形态锁 + 语义用例：带自定义状态的异常类必须能跨进程序列化（缺陷 18）。

病根：异常子类带自定义字段却没给出可重建的路径。`BaseException.__reduce__` 在
`__dict__` 非空时返回 `(cls, self.args, self.__dict__)`，反序列化会调 `cls(*self.args)`；
若 `__init__` 要的参数与 `args`（往往是格式化后的 message）对不上 → TypeError，
**炸成另一个异常、掩盖真因**。跨进程（多进程 worker / 队列 / 结果回传）时正是这个场景。

两层（AGENTS §四「形态锁 + 语义用例」）：
  1. 形态层（静态 AST 全仓普查）：带自定义状态的异常类集合，必须与 `_REGISTRY` 完全相等。
     新增一个有自定义字段的异常类而不登记即红 —— 堵「漏一个」。
  2. 语义层（真 pickle 往返）：每个登记类造实例句 → dumps/loads，断言类型不变、
     `vars()` 自定义字段逐字不丢、`str()` 不变。形态层只保「没漏」，真正判据在这一层。

与用户给的判据的偏差（有意）：判据写成「带自定义字段即须通过往返」，而非「无 __reduce__
即红」—— 后者是代理指标，会误伤。反例：DistillError / CollectionUnusableError 带自定义
字段但额外参数可默认，`cls(*args)` 本就成立、无需 __reduce__ 也 picklable。锁锁症状
（能不能往返），不锁代理（有没有钩子）。

变异：删掉任一带 __reduce__ 的类的 __reduce__（如 IncompleteResponseError / StoreError）
→ 语义层红。
"""
import ast
import importlib
import pathlib
import pickle

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# 排除目录：vendored 第三方（services/gptsovits，22738 个 .py、未入库）与构建/缓存产物。
# 只扫本仓库自有代码 —— 第三方异常的序列化行为不归我们管，也不该由本锁红绿。
_PRUNED_DIRS = {"gptsovits", "node_modules", ".git", "__pycache__", ".venv", "venv",
                "site-packages", "dist", "build", ".mypy_cache", ".pytest_cache"}

_EXC_SUFFIX = ("Error", "Exception", "Warning", "Exit", "Interrupt")

# 登记表：(相对路径, 类名) -> (导入名, 造实例的工厂)。
# 键必须与静态普查结果**完全相等**：多一个（陈旧）或少一个（漏登记）都红。
_REGISTRY = {
    ("adapters/llm_adapter.py", "IncompleteResponseError"): (
        "adapters.llm_adapter",
        lambda cls: cls("length", "gen_character", "已生成的部分正文"),
    ),
    ("core/distiller.py", "DistillError"): (
        "core.distiller",
        lambda cls: cls("用户可读消息", "ops 细节"),
    ),
    ("core/rag.py", "CollectionUnusableError"): (
        "core.rag",
        lambda cls: cls("集合不可用", collection_name="cards", stored_dim=3, expected_dim=4),
    ),
    ("storage/base.py", "StoreError"): (
        "storage.base",
        lambda cls: cls("save_text", ValueError("boom")),
    ),
    ("tests/test_embeddings.py", "_RateLimit429"): (
        "test_embeddings",
        lambda cls: cls(),
    ),
    ("tests/test_embeddings.py", "_BadRequest400"): (
        "test_embeddings",
        lambda cls: cls(),
    ),
    ("tests/test_llm_adapter_retry.py", "_RateLimitError429"): (
        "test_llm_adapter_retry",
        lambda cls: cls("0"),
    ),
}


def _is_exc_name(name: str) -> bool:
    return name.endswith(_EXC_SUFFIX) or name == "BaseException"


def _exception_classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    """本文件里定义的 exception 类（含本地继承链：子类继承本文件内的异常基类）。"""
    known: dict[str, ast.ClassDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names = [b.id if isinstance(b, ast.Name) else b.attr
                 for b in node.bases if isinstance(b, (ast.Name, ast.Attribute))]
        if any(_is_exc_name(n) for n in names) or any(n in known for n in names):
            known[node.name] = node
    return known


def _has_custom_state(cls: ast.ClassDef) -> bool:
    """自定义 __init__ 收额外位置参数，或往 self.X 挂属性 —— 即「带自定义字段」。"""
    init = next((f for f in cls.body
                 if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == "__init__"),
                None)
    if init is None:
        return False
    if [a for a in init.args.posonlyargs + init.args.args if a.arg != "self"]:
        return True
    return any(isinstance(s, ast.Assign)
               and any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                       and t.value.id == "self" for t in s.targets)
               for s in ast.walk(init))


def _census() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for p in sorted(REPO_ROOT.rglob("*.py")):
        if set(p.relative_to(REPO_ROOT).parts) & _PRUNED_DIRS:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for name, cls in _exception_classes(tree).items():
            if _has_custom_state(cls):
                found.add((p.relative_to(REPO_ROOT).as_posix(), name))
    return found


def _resolve(module_name: str, class_name: str):
    return getattr(importlib.import_module(module_name), class_name)


def test_census_matches_registry():
    """形态层：全仓带自定义状态的异常类 == 登记表（无漏登、无陈旧）。"""
    census = _census()
    registered = set(_REGISTRY)
    assert census, "普查为空 —— 扫描范围有盲区，锁在假绿"
    assert census - registered == set(), (
        f"新出现带自定义状态的异常类未登记（可能跨进程变脸）：{sorted(census - registered)}")
    assert registered - census == set(), (
        f"登记表有陈旧项（类已删/改名/不再带自定义状态）：{sorted(registered - census)}")


@pytest.mark.parametrize("key", sorted(_REGISTRY), ids=lambda k: k[1])
def test_exception_survives_pickle_roundtrip(key):
    """语义层：真往返后类型/自定义字段/str 全不变 —— 跨进程不变脸。"""
    rel_path, cls_name = key
    module_name, factory = _REGISTRY[key]
    cls = _resolve(module_name, cls_name)
    assert cls.__module__ and cls.__name__ == cls_name

    original = factory(cls)
    restored = pickle.loads(pickle.dumps(original))

    assert type(restored) is cls, f"{cls_name} 往返后类型变了：{type(restored)}"
    assert vars(restored) == vars(original), (
        f"{cls_name} 往返后自定义字段丢失：{vars(original)} -> {vars(restored)}")
    assert str(restored) == str(original)
