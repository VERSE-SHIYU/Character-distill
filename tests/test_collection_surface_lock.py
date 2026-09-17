# -*- coding: utf-8 -*-
"""元锁：**长得像测试的文件都真的会被跑** —— 「像个测试」与「在收集面内」两个集合必须闭合。

**它防的是什么。** `web/test_spa_fallback.py` 有 5 条 `def test_*`、覆盖 5 条真命题
（静态资源优先于 catch-all / 中文文件名 / SPA 回退 / `/api/*` 不被回退吃掉 / 路径穿越被挡），
却因为躺在 `web/` 而不被收集 —— `pytest.ini` 的 `testpaths = tests` 把它排在面外，
全仓没有任何 workflow / 脚本跑过它。它的 `__main__` 运行器保住了「能跑」，没保住「会被跑」。
**不被收集的测试与没有测试是一回事**（§四「一条恒 skip 的锁和没有锁是一回事」的邻居）。

**命题不是「那个文件放错地方」**（那是症状），是「有文件长得像测试但不会被跑」——后者是**一类**。
所以判据写在这**一类**上，不写在那个文件上：那个文件只是今天唯一的实例。

**为什么不直接把文件挪进来**（用户裁定，见 AGENTS.md 缺陷 43）。`_STATIC_DIR` 是
`web/server.py` 的模块级常量，而这个文件 `from server import app, _STATIC_DIR` —— 移文件要
干净就得改生产代码，为整理测试位置改生产代码是本末倒置。于是判据留在原地，被豁免的那条
（**只有一条**）登记在下面 `_OUT_OF_SURFACE`。

**两个事实怎么读**（都从 `pytestconfig` 现读，一个都不写死）：
  - **收集面**：`testpaths` 声明的目录树。本机裸 `pytest` 走它；CI 打的是 `pytest tests/`
    （显式路径让 `testpaths` 不生效），两条入口指向同一个目录，故读声明面对两者都成立。
    这条锁看不见的漂移：将来有人把 CI 改成 `pytest 别的目录/` —— 那处不一致不在本判据内。
  - **「像个测试」**：文件名匹配 `python_files` **且**模块体里有匹配 `python_functions` /
    `python_classes` 的可收集条目。两个条件都要，各自都有实测的反例：
    光看文件名会误伤 `scripts/test_distill.py`（吃 `sys.argv[1]` 的手工脚本）；
    光看函数名会误伤 `web/server.py`（三个 `async def test_*_connection` 是**路由处理器**，
    正好叫这个名字）。见 `test_the_two_predicates_are_both_load_bearing`。
"""

from __future__ import annotations

import ast
import fnmatch
import os
import pathlib

import policy_table

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 与 tests/test_exception_pickle_lock.py 逐字同一份排除表。它是**扫描范围**，不是「豁免」——
# 第三方 vendored 代码与构建/缓存产物本来就不该进任何普查，与「有意不收集的测试」是两回事。
_PRUNED_DIRS = {"gptsovits", "node_modules", ".git", "__pycache__", ".venv", "venv",
                "site-packages", "dist", "build", ".mypy_cache", ".pytest_cache"}

# 有意留在收集面外的**测试**：键 = 仓库相对路径，值 = 为什么它必须留在外面。
# 机械校验交给 tests/policy_table.py（本仓「人声明的豁免表」的单一出口，不另造一套）。
# **名单只许减少**（同 tests/lock_coverage_gaps.py）：条目一旦不再是缺口就必须删，
# 否则它会退化成只增不减的手工清单。
_OUT_OF_SURFACE = {
    "web/test_spa_fallback.py": (
        "自带 __main__ 运行器，`python web/test_spa_fallback.py` 是它的既定用法；覆盖的 5 条"
        "命题都还有价值，只是没有任何自动化在跑它。移进 tests/ 要动生产常量 "
        "web/server.py 的 _STATIC_DIR 才干净 —— 为整理测试位置改生产代码是本末倒置"
        "（缺陷 43 的用户裁定：不移动文件，加判据）。"
    ),
}


def _matches(name: str, patterns) -> bool:
    """pytest 自己的名字匹配规则：先 `startswith`，含通配符才走 fnmatch。

    照抄 `PyobjMixin._matches_prefix_or_glob_option` —— `python_functions` 的默认值是
    `["test"]`（前缀，**不是** `test*`），拿 fnmatch 去套会一个都不匹配。
    """
    for opt in patterns:
        if name.startswith(opt):
            return True
        if ("*" in opt or "?" in opt or "[" in opt) and fnmatch.fnmatch(name, opt):
            return True
    return False


def _has_collectable_item(tree: ast.Module, func_pats, class_pats) -> bool:
    """模块体里有可收集条目：匹配 `python_functions` 的函数，或匹配 `python_classes` 的类
    里有这种函数。

    只取**模块体**（`tree.body`）—— pytest 也是从模块的 `dir()` 里取的；嵌在别的函数里的
    `def test_*` 不可收集，收进来就是凭空多出一条「缺口」。
    """
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _matches(node.name, func_pats):
                return True
        elif isinstance(node, ast.ClassDef) and _matches(node.name, class_pats):
            if any(isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and _matches(b.name, func_pats) for b in node.body):
                return True
    return False


def _walk(root: pathlib.Path):
    """遍历仓库自有的 `.py`，**在走之前**剪掉 `_PRUNED_DIRS`。

    与 `tests/test_exception_pickle_lock.py` 等既有普查同一份排除表，只有遍历方式不同：
    那边用 `REPO_ROOT.rglob("*.py")` 再逐条筛（走完全树才筛），这里用 `os.walk` +
    `dirs[:]` 原地剪枝 —— **实测快一个数量级以上，结果同**（`2026-09-15 实测 167×；
    2026-09-17 复测 36×、同日再测 67×` —— 比值随手一测就翻倍，**这类数字不是指标**，
    别拿它当验收门槛）。「结果同」不是目视：两法产出的路径集合逐元素相等（322 个，
    差集两向皆空）。差值全在 `services/gptsovits`（22738 个不入库的 .py）与 `.venv` 上：
    rglob 会把它们走完再丢掉，而本锁一次收集要跑三遍。

    「为什么只改这一把锁、其余仍是 rglob」是**决策**，写在 AGENTS.md 缺陷 43 条目；
    这里只写**机制**（§四「同一个理由不要落在三个地方」）。
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNED_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield pathlib.Path(dirpath) / name


def _census(config) -> tuple[set[str], set[str]]:
    """(收集面内的, 收集面外的) 两组「像个测试的文件」，仓库相对 posix 路径。

    面外只看目录归属 —— 与 pytest 一致，它也是从 `testpaths` 起走目录树。**逐案的**排除面
    （`--ignore` / 嵌套 `collect_ignore`）不在本判据内：本仓今天一处都没有
    （`git grep 'collect_ignore'` 零命中），真出现了它会以「文件在目录里却没被跑」的形态
    绕过这条锁 —— 那时再补，不提前造机制。
    """
    root: pathlib.Path = config.rootpath
    surface = [(root / p).resolve() for p in config.getini("testpaths")]
    file_pats = config.getini("python_files")
    func_pats = config.getini("python_functions")
    class_pats = config.getini("python_classes")

    inside: set[str] = set()
    outside: set[str] = set()
    for path in sorted(_walk(root)):
        if not _matches(path.name, file_pats):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if not _has_collectable_item(tree, func_pats, class_pats):
            continue
        (inside if any(path.resolve().is_relative_to(d) for d in surface) else outside)\
            .add(path.relative_to(root).as_posix())
    return inside, outside


# ── 自证：普查本身得是活的 ──────────────────────────────────────────────────

def test_the_census_finds_the_lock_itself(pytestconfig):
    """扫描整片失效时下面两条会**恒绿**（恒绿的锁与没有锁是一回事），所以先钉住它活着。

    自证不靠魔法数字（「至少扫到 N 个文件」那种会被合法改动静默绕过）：本文件永远存在、
    永远叫 `test_*.py`、永远有 `def test_*` —— 它扫不到自己就一定是范围出了盲区。
    """
    inside, _ = _census(pytestconfig)
    me = pathlib.Path(__file__).resolve().relative_to(pytestconfig.rootpath).as_posix()
    assert me in inside, (
        f"普查没扫到本文件（{me}）—— 扫描范围有盲区，下面两条判据在假绿。"
        f"扫到的面内文件共 {len(inside)} 个：{sorted(inside)[:5]}…")


# ── 命题：面外的「像个测试的文件」集合 == 名单（两向都查）────────────────────

def test_every_test_shaped_file_is_inside_the_collection_surface(pytestconfig):
    """**现场有、名单没有** → 点名那条新出现的「不会被跑的文件」。"""
    _, outside = _census(pytestconfig)
    new = policy_table.unexpected(outside, _OUT_OF_SURFACE)
    assert not new, (
        "这些文件长得像测试，却不在 pytest 的收集面里 —— **不会被任何东西跑**"
        f"（testpaths = {'、'.join(pytestconfig.getini('testpaths'))}）：\n"
        + "\n".join(f"  {rel}" for rel in sorted(new))
        + "\n处置只有两种：把它挪进收集面，或在本文件的 _OUT_OF_SURFACE 里登记并写清"
          "「为什么它必须留在外面」。**不处置 = 它继续不被跑。**")


def test_the_list_only_shrinks(pytestconfig):
    """**名单有、现场没有** → 条目已经不是缺口了，必须删（名单只许减少）。

    这一条才是关键：只查一个方向的话，名单会退化成只增不减的手工清单（本仓踩过两次的形态）。
    实测形态 —— 把 `web/test_spa_fallback.py` 挪进 `tests/`：方向 ① 立刻变绿，而这一条
    点名那行、要人删掉它；删完两条都绿。**「已修」这件事由人确认，不由判据猜。**
    """
    _, outside = _census(pytestconfig)
    stale = policy_table.stale_keys(_OUT_OF_SURFACE, outside)
    assert not stale, (
        "_OUT_OF_SURFACE 里这些条目已经不是「收集面外的测试」了（挪进去了 / 改名了 / 删了）"
        "—— 名单只许减少，把这几行删掉：\n"
        + "\n".join(f"  {rel}" for rel in sorted(stale)))


def test_every_entry_states_a_reason():
    """空理由 = 没有理由（`tests/policy_table.empty_reasons`：先判类型再判内容）。"""
    empty = policy_table.empty_reasons(_OUT_OF_SURFACE)
    assert not empty, f"_OUT_OF_SURFACE 里这些条目没写理由：{sorted(empty)}"


# ── 两个谓词都是承重的（正控 + 负控，全部走合成输入，不碰仓内）────────────────

_SYNTH_ROUTE_HANDLER = '''\
async def test_gptsovits_connection(req):
    return {"ok": True}


async def test_funasr_connection(req):
    return {"ok": True}
'''

_SYNTH_SCRIPT = '''\
import sys

TEXT_ID = sys.argv[1] if len(sys.argv) > 1 else "x"


def main():
    print(TEXT_ID)


if __name__ == "__main__":
    main()
'''

_SYNTH_CLASS_ONLY = '''\
class TestThing:
    def test_it(self):
        assert True
'''

_SYNTH_NESTED = '''\
def make_suite():
    def test_inner():
        assert True
    return test_inner
'''


def test_the_two_predicates_are_both_load_bearing():
    """缺任一个谓词都会给出错答案 —— 四条合成输入各钉一个方向（实测出来的真反例形态）。

    - `web/server.py` 那三条 `async def test_*_connection`：**有**函数名、**不在** `test_*.py`
      里 ⇒ 只看函数名会把它判成「不会被跑的测试」（假阳性，逼人去登记一堆路由处理器）。
    - `scripts/test_distill.py`：**在** `test_*.py` 里、**没有**可收集条目（只有 `main()`）
      ⇒ 只看文件名会把它判成缺口（同上）。
    - `tests/test_admin_tasks_api.py` 那一类：整个文件只有 `class Test*`
      ⇒ 只查模块级函数的普查会漏掉它们（**假阴性**——比假阳性更坏，那是真的失守）。
    - 嵌在别的函数里的 `def test_*`：`pytest` 收不到，收进普查就是**凭空造缺口**。
    """
    func_pats, class_pats = ["test"], ["Test"]
    file_pats = ["test_*.py", "*_test.py"]

    def shaped(src: str, filename: str) -> bool:
        if not _matches(filename, file_pats):
            return False
        return _has_collectable_item(ast.parse(src), func_pats, class_pats)

    # 文件名不是 test_*.py ⇒ 再像也不进普查（路由处理器就是这个形态）
    assert not shaped(_SYNTH_ROUTE_HANDLER, "server.py")
    # 文件名是 test_*.py 但无可收集条目 ⇒ 不进普查（手工脚本就是这个形态）
    assert not shaped(_SYNTH_SCRIPT, "test_distill.py")
    # 只有 class Test* ⇒ 必须在普查里（只查模块级函数会漏掉这一类）
    assert shaped(_SYNTH_CLASS_ONLY, "test_admin_like.py")
    # 嵌在函数里的 def test_* ⇒ 不可收集，不许进普查
    assert not shaped(_SYNTH_NESTED, "test_nested.py")
    # 正控：最普通的形态必须在
    assert shaped("def test_x():\n    assert True\n", "test_plain.py")
