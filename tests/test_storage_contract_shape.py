# -*- coding: utf-8 -*-
"""锁：`StorageBase` 的每个抽象方法，在**两个实现里形参表必须逐格相同**。

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

**判据怎么从事实推出（不维护函数名单）。** 抽象方法集来自
`StorageBase.__abstractmethods__` —— 那是 `ABCMeta` 按真实的 `@abstractmethod`
声明算出来的，不靠人抄。新增一个抽象方法、或给它加第三个实现，自动进判据面。

**唯一的例外见 `PRE_EXISTING_GAPS`**：建立本判据那一刻**已经存在**的契约缺口，
按既有规矩可以入册（名单只收建立时既有的缺口，此后新增的一律不许入册）。
每一条都是 `strict=True` 的 xfail —— 缺口一旦补上，那条会以 XPASS 变红，
逼着人把册子里的名字删掉，**册子不会变成新债的收容所**。
"""

from __future__ import annotations

import inspect

import pytest

from storage.base import StorageBase
from storage.postgres_store import PostgresStore
from storage.sqlite_store import SQLiteStore

IMPLS = (SQLiteStore, PostgresStore)

# 建立本判据时**已经存在**的契约缺口。判据：(a) 建立时刻就已存在，(b) 补上后被强制出册。
# `save_text` 的 base 少声明两个尾部参数，两个实现多出来 —— **纯尾部追加**，
# 前缀逐格相同，所以 base 派调用点能到的位置没有一个改变含义（不像 `save_message`
# 的互换、`create_user` 的中间插入）。**是契约不完整，不是契约漂移**，会响不会哑，
# 故本轮记册不修（见 AGENTS.md 条目 58 重核段）。
PRE_EXISTING_GAPS = {
    "save_text": (
        "base 少声明 content_resolved / coref_resolved 两个尾部参数（两个实现都有）。"
        "纯尾部追加，前缀逐格相同，无静默错位风险 —— 补齐 base 声明即可出册。"
    ),
}

_ABSTRACT_NAMES = sorted(StorageBase.__abstractmethods__)


def _params(name: str):
    """把抽象方法参数化成 `pytest.param`，带册内标记。"""
    if name in PRE_EXISTING_GAPS:
        return pytest.param(
            name, marks=pytest.mark.xfail(strict=True, reason=PRE_EXISTING_GAPS[name]),
            id=f"{name}-known-gap",
        )
    return pytest.param(name, id=name)


def _shape(fn: object) -> list[tuple[str, str, bool, object]]:
    """形参表：`(名字, 种类, 有无默认值, 默认值)`，逐格可比。"""
    rows = []
    for name, p in inspect.signature(fn).parameters.items():
        has_default = p.default is not inspect.Parameter.empty
        rows.append((name, p.kind.name, has_default, p.default if has_default else None))
    return rows


def _diff(base: list, impl: list) -> str:
    """第一处差异，带位置 —— 报错要能直接指到是哪一格。"""
    for i in range(max(len(base), len(impl))):
        b = base[i] if i < len(base) else ("<缺>", "", False, None)
        s = impl[i] if i < len(impl) else ("<缺>", "", False, None)
        if b != s:
            return f"第 {i} 格：base={b!r} / impl={s!r}"
    return "（无差异）"


def test_the_gap_registry_holds_only_real_gaps():
    """册子自清：补上的缺口必须从 `PRE_EXISTING_GAPS` 里删掉，否则本条红。"""
    stale = [n for n in PRE_EXISTING_GAPS if n not in _ABSTRACT_NAMES]
    assert not stale, (
        f"这些名字已经不是抽象方法了，却还留在册子里：{stale}。"
        "册子只收建立判据时既有的缺口；缺口消失就该出册。"
    )


@pytest.mark.parametrize("name", [_params(n) for n in _ABSTRACT_NAMES])
def test_impl_signature_matches_base(name: str):
    """锁本体：抽象方法与每个实现的形参表逐格相同。"""
    base = _shape(getattr(StorageBase, name))
    for cls in IMPLS:
        node = getattr(cls, name, None)
        assert node is not None, (
            f"`{cls.__name__}` 没有实现 `StorageBase.{name}`。"
            "抽象方法集由 `__abstractmethods__` 现算，所以这里意味着实现缺失。"
        )
        impl = _shape(node)
        assert impl == base, (
            f"`{name}` 在 `{cls.__name__}` 里的形参表与 `StorageBase` 不同 —— {_diff(base, impl)}\n"
            f"  base : {[r[0] + ('=' if r[2] else '') for r in base]}\n"
            f"  impl : {[r[0] + ('=' if r[2] else '') for r in impl]}\n"
            "抽象基类是契约：照 base 写的调用点（或第三个实现）会按 base 的格子绑定，\n"
            "而实现按自己的格子取值 —— 名字相近、类型相近时**不报错，只静默装错值**。\n"
            "修法是让三处形参表逐格一致，并给可选参数加 `*`（keyword-only）把调用点也钉住。"
        )


def test_implementations_leave_nothing_abstract():
    """两个实现都不许再留抽象方法 —— 否则上面那条会以为「实现了」而放过。"""
    for cls in IMPLS:
        assert not cls.__abstractmethods__, (
            f"`{cls.__name__}` 仍是抽象的：{sorted(cls.__abstractmethods__)}"
        )
