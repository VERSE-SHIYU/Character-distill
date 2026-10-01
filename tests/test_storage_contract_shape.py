# -*- coding: utf-8 -*-
"""锁：`StorageBase` 的每个抽象方法，在**每个登记的实现里形参表必须逐格相同**。

**它防的是什么。** 抽象基类是契约 —— 但契约漂移不会报错。2026-09 核出两处：

  - `save_message`：base 声明第 5 位是 `retracted`，两个实现第 5 位是 `reply_to_id`
    （第 5/6 位互换）。
  - `create_user`：两个实现在第 5 位插入了 `email`，base 没有这一格。

两处**今天都不出事**，只因为两个实现彼此一致、而调用点照实现写。照 base 写第三个实现
（或 `create_user` 那样：照 base 按位置传 4 个实参），第 5 格的含义就变了 ——
`home_region` 的值静默落进 `email`，**不报错**。这与 `_create_session` 那次
（`embedding_key` 落进 `user_role`，缺陷 G）是同一形态。

**为什么判据是「形参表逐格相同」而不是「可选参数 keyword-only」。** 后者只防调用点
错位，防不了契约漂移；前者把两者一起管住 —— 逐格比的是
`(名字, 种类, 有无默认值, 默认值)`，种类里就含 `KEYWORD_ONLY`，
所以「谁把 `*` 撤了」「谁把顺序换了」都会红。**形参表相同才是根因。**

**判据面从哪来（方法集现算、实现类显式登记）。** 抽象方法集来自
`StorageBase.__abstractmethods__` —— `ABCMeta` 按真实的 `@abstractmethod` 声明算出来，
不靠人抄：新增一个抽象方法自动进判据面。实现类则是一份**显式清单** `IMPLEMENTATIONS`，
在模块顶部直接 `import` 两个后端。

**非抽象公开方法也进判据面 —— 但只比「被覆写的」。** `StorageBase` 上的非抽象公开方法
若被某个实现在自己的 `__dict__` 里覆写，则同样逐格比签名；未覆写就跳过，不要求必须覆写
（否则等于把便利实现升格成强制契约）。今天这类方法为空，故参比的方法集与旧版逐字一致。

**为什么实现类显式登记，而不是 `StorageBase.__subclasses__()` 现算。** 后者现算的是
「**当前进程里加载了哪些子类**」，不是「仓库里有哪些实现」：判据的粗细随导入顺序漂移，
测试替身（`tests/test_storage_ping.py` 里 `type(...)` 造的 `_StubStore`）一旦先被加载就会
被卷进来。显式 `import` 把「检查谁」钉在源码上，与进程状态无关。
**代价（写清，这是有意的取舍）**：新增第三个后端时要手工在 `IMPLEMENTATIONS` 里加一行 ——
但「忘了加」不会静默漏过：`test_registered_implementations_cover_the_storage_package`
用 `pkgutil.walk_packages` 扫 `storage` 包里定义的具体子类，与清单**恰好相等**才绿。

**唯一的例外见 `PRE_EXISTING_GAPS`**：建立本判据那一刻**已经存在**的契约缺口，
按既有规矩可以入册（名单只收建立时既有的缺口，此后新增的一律不许入册）。键是
`(实现类名, 方法名)`，与参数化粒度一致。每一条都是 `strict=True` 的 xfail —— 缺口一旦
补上，那条会以 XPASS 变红，逼着人把册子里的名字删掉，**册子不会变成新债的收容所**。
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import storage
from storage.base import StorageBase
from storage.postgres_store import PostgresStore
from storage.sqlite_store import SQLiteStore

# 判据面里的实现类 —— 显式登记，不靠 `__subclasses__()`（理由见模块 docstring）。
# 只有**在自己的 `__dict__` 里定义全部抽象方法**的类才放得进来（继承自另一个实现的
# 子类不算，见 `test_impl_signature_...`）。少登记/多登记由
# `test_registered_implementations_cover_the_storage_package` 守着。
IMPLEMENTATIONS: list[type] = [PostgresStore, SQLiteStore]

# 建立本判据时**已经存在**的契约缺口，键为 `(实现类名, 方法名)`。判据：(a) 建立时刻就已
# 存在，(b) 补上后被强制出册。唯一一条曾是 `save_text`：base 少声明 content_resolved /
# coref_resolved 两个尾部参数，两个实现多出来。87 退役这两列时两个实现同步删掉，base 与
# 实现逐格相同，缺口随之出册 —— 册子恢复为空不是「暂时没人踩」，是那处不对称已从代码里
# 消失。机制留在原地：下一个真实缺口照旧登记在这里，
# `test_the_gap_registry_holds_only_real_gaps` 负责清册。
PRE_EXISTING_GAPS: dict[tuple[str, str], str] = {}

_ABSTRACT_NAMES = sorted(StorageBase.__abstractmethods__)


def _public_concrete_names(cls: type) -> list[str]:
    """`cls` 上非抽象、公开（不以 `_` 开头）的可调用成员名 —— 契约里的便利实现位。"""
    return sorted(
        n for n, v in cls.__dict__.items()
        if not n.startswith("_") and callable(v) and n not in cls.__abstractmethods__
    )


_CONCRETE_NAMES = _public_concrete_names(StorageBase)


def _comparable_names(impl: type) -> list[tuple[str, bool]]:
    """该实现要参比的 `(方法名, 是否必须自带实现)`。

    抽象方法：必须自带（`required=True` —— 继承来的不算实现）。
    非抽象公开方法：只有当本实现覆写了它才参比（`required=False`）—— 未覆写就跳过，
    不要求必须覆写。
    """
    rows = [(n, True) for n in _ABSTRACT_NAMES]
    rows += [(n, False) for n in _CONCRETE_NAMES if n in impl.__dict__]
    return rows


def _case(impl: type, name: str, required: bool) -> pytest.param:
    """把 `(实现, 方法, 是否必须自带)` 参数化成 `pytest.param`，id 为 `类名.方法名`。"""
    key = (impl.__name__, name)
    if key in PRE_EXISTING_GAPS:
        return pytest.param(
            impl, name, required,
            marks=pytest.mark.xfail(strict=True, reason=PRE_EXISTING_GAPS[key]),
            id=f"{impl.__name__}.{name}-known-gap",
        )
    return pytest.param(impl, name, required, id=f"{impl.__name__}.{name}")


_CASES = [
    _case(impl, n, req) for impl in IMPLEMENTATIONS for (n, req) in _comparable_names(impl)
]


def _sig(fn: object) -> inspect.Signature:
    """签名；`eval_str=True` 把 `from __future__ import annotations` 的字符串标注求值成对象。"""
    return inspect.signature(fn, eval_str=True)


def _first_difference(name: str, base: inspect.Signature, impl: inspect.Signature) -> str | None:
    """逐参数比 `(名字, 种类, 默认值, 标注)`，再比返回标注；返回第一处差异，无差异返回 None。"""
    bp = list(base.parameters.values())
    ip = list(impl.parameters.values())
    for i in range(max(len(bp), len(ip))):
        b = bp[i] if i < len(bp) else None
        s = ip[i] if i < len(ip) else None
        if b is None or s is None:
            exp = f"参数 {b.name!r}" if b is not None else "<缺>"
            act = f"参数 {s.name!r}" if s is not None else "<缺>"
            return f"方法 {name} 第 {i} 个参数：期望 {exp}，实际 {act}"
        for field in ("name", "kind", "default", "annotation"):
            bv, sv = getattr(b, field), getattr(s, field)
            if bv != sv:
                return (
                    f"方法 {name} 第 {i} 个参数（{b.name}）的 {field}：期望 {bv!r}，实际 {sv!r}"
                )
    if base.return_annotation != impl.return_annotation:
        return (
            f"方法 {name} 的返回标注：期望 {base.return_annotation!r}，"
            f"实际 {impl.return_annotation!r}"
        )
    return None


def test_the_gap_registry_holds_only_real_gaps():
    """册子自清：补上的缺口必须从 `PRE_EXISTING_GAPS` 里删掉，否则本条红。"""
    live = {
        (impl.__name__, n)
        for impl in IMPLEMENTATIONS
        for n, _ in _comparable_names(impl)
    }
    stale = [k for k in PRE_EXISTING_GAPS if k not in live]
    assert not stale, (
        f"这些 (实现, 方法) 已不在判据面里，却还留在册子里：{stale}。"
        "册子只收建立判据时既有的缺口；缺口消失就该出册。"
    )


@pytest.mark.parametrize("impl,name,required", _CASES)
def test_impl_signature_matches_base(impl: type, name: str, required: bool):
    """锁本体：抽象方法与每个实现的签名逐格相同（类级比对，不实例化）。"""
    if required:
        assert name in impl.__dict__, (
            f"`{impl.__name__}` 没有在自己的 `__dict__` 里定义 `{name}`。"
            "抽象基类里的声明不是实现，继承别的实现也不算 —— 要进 `IMPLEMENTATIONS` 就得"
            "自己把契约写全。"
        )
    # required=False 的非抽象方法：参数化时已确认本实现覆写了它，才生成这条用例。
    base_fn = getattr(StorageBase, name)
    impl_fn = impl.__dict__[name]
    base_async = inspect.iscoroutinefunction(base_fn)
    impl_async = inspect.iscoroutinefunction(impl_fn)
    assert impl_async == base_async, (
        f"方法 {name} 的同步/异步属性两侧不一致："
        f"base={'async' if base_async else 'sync'}，"
        f"{impl.__name__}={'async' if impl_async else 'sync'}"
    )
    diff = _first_difference(name, _sig(base_fn), _sig(impl_fn))
    assert diff is None, (
        f"`{impl.__name__}.{name}` 与 `StorageBase.{name}` 的签名不一致 —— {diff}\n"
        "抽象基类是契约：照 base 写的调用点（或第三个实现）会按 base 的格子绑定，\n"
        "而实现按自己的格子取值 —— 名字相近、类型相近时**不报错，只静默装错值**。\n"
        "修法是让 base 与实现逐格一致，并给可选参数加 `*`（keyword-only）把调用点也钉住。"
    )


@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda c: c.__name__)
def test_implementation_leaves_nothing_abstract(impl: type):
    """每个登记的实现都不许再留抽象方法 —— 否则上面那条会以为「实现了」而放过。"""
    assert not impl.__abstractmethods__, (
        f"`{impl.__name__}` 仍是抽象的：{sorted(impl.__abstractmethods__)}"
    )


def _storage_package_implementations() -> set[type]:
    """导入 `storage` 包根与包下所有模块，收集**定义在 `storage` 里**的具体 `StorageBase` 子类。

    `pkgutil.walk_packages` 只遍历**子模块**，不含包根 `storage/__init__.py` 本身 ——
    故把 `storage` 模块显式并进去一并扫描。模块判据按 `__module__`：等于 `storage`
    或以 `storage.` 开头 —— 测试替身（定义在 `tests.*`）即便继承 `StorageBase`
    也不会被算进来。
    """
    modules = [storage]
    modules += [
        importlib.import_module(mi.name)
        for mi in pkgutil.walk_packages(storage.__path__, prefix=storage.__name__ + ".")
    ]
    found: set[type] = set()
    for module in modules:
        for obj in vars(module).values():
            owner = getattr(obj, "__module__", "")
            if (
                isinstance(obj, type)
                and obj is not StorageBase
                and issubclass(obj, StorageBase)
                and (owner == storage.__name__ or owner.startswith(storage.__name__ + "."))
                and not obj.__abstractmethods__
            ):
                found.add(obj)
    return found


def test_registered_implementations_cover_the_storage_package():
    """`IMPLEMENTATIONS` 必须**恰好**等于 `storage` 包里定义的具体子类 —— 不多不少。

    这是「自动发现」被换成显式清单后补上的那一环：清单本身是漂移点，这条守卫保证
    「新后端忘了登记」会红并点名，而不是静默漏出判据面。
    """
    found = _storage_package_implementations()
    registered = set(IMPLEMENTATIONS)
    unregistered = sorted(c.__name__ for c in found - registered)
    extra = sorted(c.__name__ for c in registered - found)
    assert not unregistered and not extra, (
        "`IMPLEMENTATIONS` 与 `storage` 包里定义的具体子类对不上。"
        f"未登记（在包里、却不在清单）：{unregistered}；"
        f"多登记（在清单、包里没有）：{extra}。"
        "新增后端要在 `IMPLEMENTATIONS` 里加一行，否则「形参表逐格相同」那条锁不到它。"
    )
