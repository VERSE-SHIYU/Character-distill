"""豁免表校验层：只做「人声明的豁免表」的机械校验，**与领域无关**，不含任何业务知识。

本文件不是一个测试，`tests/` 下的辅助模块（先例 ``tests/route_facts.py``、
``tests/evidence_fakes.py``），供锁调用。

**使用方**（各自的表属于各自的策略，本层不认识表里的任何键）：
  - ``tests/test_text_failure_messages.py`` —— ``_FORM_METADATA``（哪些非正文 Form 字段可以存在）；
  - ``tests/test_auth_param_used.py`` —— ``ALLOWLIST``（哪些端点注入了身份参数却有意不引用）；
  - ``tests/test_policy_table.py`` —— 本层自己的锁（合成表）。

**为什么需要这一层。** 那两张表要回答的是同一组问题 —— 「表里有没有现场已经不出现的陈旧
条目」「表里有没有人写了空理由」「现场出现的东西有没有没登记的」—— 而两把锁**各写了一遍**
这套差集与判空。两份实现会各自漂移，且漂移**不报错**：只让两把锁对同一件事给出不同答案。
这是缺陷 42 第 3b 步收掉的那种形态（同一判定两份实现）的第二处。

**边界。** 本文件的函数不认识任何具体路径、字段名或依赖名 —— 哪些键该被豁免、理由写什么，
是各把锁自己的策略（与 ``route_facts`` 同一条原则：共享层只提供机械操作）。表统一是
**扁平映射** ``dict[Hashable, str]``：键 = 被豁免的那个东西（可以是元组，也可以是复合键），
值 = 人写的理由。
"""

from __future__ import annotations

from typing import Hashable, Iterable, Mapping


def empty_reasons(table: Mapping[Hashable, str]) -> set:
    """理由为空、只有空白字符、**或根本不是字符串**的键 —— 「写了理由」不等于「理由有内容」。

    先判类型再判内容，不做字符串化。理由是 `None` / `0` 这类非字符串时直接判为空 ——
    把它们先转成字符串再 strip，缺失值就变成了看似有内容的文本（`None` → `"None"`、
    `0` → `"0"`），于是「理由在不在」被偷换成「转成字符串之后长不长」：那是代理判据，
    不是判据本身。理由字段缺失是**结构问题**，不该长得像理由。
    """
    return {k for k, reason in table.items()
            if not (isinstance(reason, str) and reason.strip())}


def stale_keys(table: Mapping[Hashable, str], observed: Iterable) -> set:
    """表里有、但现场没出现的键 —— 被豁免的东西已经消失（或已经不再需要豁免）。"""
    return set(table) - set(observed)


def unexpected(observed: Iterable, table: Mapping[Hashable, str]) -> set:
    """现场出现了、表里却没登记的键 —— 新长出来的东西还没有人看过。"""
    return set(observed) - set(table)
