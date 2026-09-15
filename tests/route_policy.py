"""路由**策略表**校验层：只做「人声明的豁免表」的机械校验，不含任何业务知识。

本文件不是一个测试，`tests/` 下的辅助模块（先例 ``tests/route_facts.py``、
``tests/evidence_fakes.py``），供锁调用。

**为什么需要这一层。** 两把锁各有一张「我允许这样，理由是……」的表：L5 的
``_FORM_METADATA``（哪些非正文 Form 字段可以存在）与 auth 锁的 ``ALLOWLIST``（哪些端点
注入了身份参数却有意不引用）。两张表要回答的是同一组问题 —— 「表里有没有现场已经不出现的
陈旧条目」「表里有没有人写了空理由」「现场出现的东西有没有没登记的」—— 而两把锁**各写了
一遍**这套差集与判空。两份实现会各自漂移，且漂移**不报错**：只让两把锁对同一件事给出
不同答案。这是缺陷 42 第 3b 步收掉的那种形态（同一判定两份实现）的第二处。

**边界。** 本文件的函数不认识任何具体路径、字段名或依赖名 —— 哪些键该被豁免、理由写什么，
是各把锁自己的策略（与 ``route_facts`` 同一条原则：共享层只提供机械操作）。表统一是
**扁平映射** ``dict[Hashable, str]``：键 = 被豁免的那个东西（可以是元组，也可以是复合键），
值 = 人写的理由。
"""

from __future__ import annotations

from typing import Hashable, Iterable, Mapping


def empty_reasons(table: Mapping[Hashable, str]) -> set:
    """理由为空、或只有空白字符的键 —— 「写了理由」不等于「理由有内容」。"""
    return {k for k, reason in table.items() if not str(reason).strip()}


def stale_keys(table: Mapping[Hashable, str], observed: Iterable) -> set:
    """表里有、但现场没出现的键 —— 被豁免的东西已经消失（或已经不再需要豁免）。"""
    return set(table) - set(observed)


def unexpected(observed: Iterable, table: Mapping[Hashable, str]) -> set:
    """现场出现了、表里却没登记的键 —— 新长出来的东西还没有人看过。"""
    return set(observed) - set(table)
