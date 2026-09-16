# -*- coding: utf-8 -*-
"""缺陷 39：文本解析层不再负责用户文案 —— 抛「发生了什么」（一个键），文案归表。

根因不是「顺手多拼了个 `{str(e)}`」，是分层坏了：`text_manager.py` 既要解析文件、
又要决定用户看到什么话，于是同一个 `except` 同时接住「我们写的用户文案」和「库的异常
原文」（DOCX 双包：上屏成了 `"DOCX 解析失败: DOCX 文件无有效文本内容"`）。

本文件锁六件事（判据驱动，**不维护点位白名单** —— 判据从 `raise` 实参的形态、import 的
**位置**直接推出）：

  L1 实参形态（静态 AST）：`text_manager.py` 每条 `raise` 的实参必须是 `_MSG[...]`
     或 `_MSG[...].format(...)` —— 里面不得出现字符串字面量，也不得引用任何 `except`
     绑定名。这条同时堵住「库原文回到上屏串」和「文案在别处又抄一份」。
  L2 闭集：用到的键 == 表键（漏一个红、表里有陈旧项也红）。
  L3 端到端 + 线索不丢：坏 PDF / 空 DOCX / 超长走 `/api/text/upload` → 400 + `detail`
     **恰等于**表里那句、不含路径与库名；**同时**日志里必须有原始诊断（收走原文 ≠
     扔掉原文 —— 那是把「泄漏」换成「瞎」）。坏 PDF 那一条另用 **import 钩子**断言
     「没触达解析器运行时」—— 那是 L6 的运行时面，不是「本机没崩」。
  L4 非空负控：L1 的扫描必须命中 ≥ 16 条 `_MSG[...]` 形态的 raise。防「对空集断言
     恒真」的假绿（§四：凡「所有 X 都满足 P」的断言，先问 X 会不会是空集）。
  L5 表单 op（读框架自己的账本，**不写手工清单**）：全仓每一条声明了表单 content 的
     operation，正文只能走**文件字段**（由 schema 判定，不看字段名），其余 Form 字段只能
     是元数据（缺陷 40 commit 二；缺陷 42 结案改判据）。
  L6 **import 的位置**：校验失败的路径不得触达解析器 —— 模块顶层不得有第三方 import；
     每个函数里「无守卫」的第三方 import 至多一条（无守卫 = 早于该函数首条 `raise`）。
     哪些算第三方从 `requirements.txt` ∩ 该文件实际 import 推出（缺陷 41 · ④）。

为什么 L1 的别名从 import 语句解析、不写成常量：写常量就是「守卫与被守对象之间的
第二份手工清单」—— 改别名时锁不会红，只会变成假绿。
"""
from __future__ import annotations

import ast
import importlib.metadata
import os
import pathlib
import re
import sys
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
import policy_table
import route_facts
from core.text_manager import TextManager
from core.text_failure import TEXT_FAILURE_MESSAGES
from deps import get_storage
from limiter import limiter
from routers.auth import get_current_user
from routers.text import router as text_router

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TM_PATH = _ROOT / "core" / "text_manager.py"
_TM_SRC = _TM_PATH.read_text(encoding="utf-8")
_TM_TREE = ast.parse(_TM_SRC)

# 期望的 raise 条数下限：16 条 raise ValueError（4 组同文案合并成 12 键）+ 6 条裸 re-raise。
_MIN_TABLE_RAISES = 16


def _table_alias(tree: ast.Module) -> str:
    """从 `core/text_manager.py` 自己的 import 语句解析出表的别名（派生，不是清单）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core.text_failure":
            for a in node.names:
                if a.name == "TEXT_FAILURE_MESSAGES":
                    return a.asname or a.name
    raise AssertionError("core/text_manager.py 没有 import TEXT_FAILURE_MESSAGES —— 锁的定位锚点没了")


_ALIAS = _table_alias(_TM_TREE)


def _is_table_lookup(node: ast.expr) -> bool:
    """`_MSG["key"]` 或 `_MSG["key"].format(...)`。"""
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "format":
            return _is_table_lookup(fn.value)
        return False
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == _ALIAS
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    )


def _raise_sites() -> list[ast.Raise]:
    """有实参的 raise（裸 `raise` 是 re-raise，不产文案，不入本锁）。"""
    return [n for n in ast.walk(_TM_TREE) if isinstance(n, ast.Raise) and n.exc is not None]


def _raise_arg(node: ast.Raise) -> ast.expr:
    """`raise ValueError(_MSG[key])` 里那个 `_MSG[key]` —— 异常构造器的第一个位置参数。

    锁的对象是**喂给异常构造器的实参**，不是 `ValueError(...)` 这个调用本身。
    """
    exc = node.exc
    assert isinstance(exc, ast.Call) and exc.args, (
        f"{node.lineno}: raise 的不是带实参的异常构造调用：{ast.unparse(exc)[:80]}")
    return exc.args[0]


def _used_keys() -> set[str]:
    keys: set[str] = set()
    for node in _raise_sites():
        for sub in ast.walk(node.exc):
            if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name)
                    and sub.value.id == _ALIAS
                    and isinstance(sub.slice, ast.Constant)
                    and isinstance(sub.slice.value, str)):
                keys.add(sub.slice.value)
    return keys


# ── L1：raise 实参形态 ─────────────────────────────────────────────────────


def test_l1_every_raise_arg_is_a_table_lookup():
    """每条 raise 只能抛「一个键」，不能抛「一句话」—— 字面量与库原文都无处可写。"""
    bad = []
    for node in _raise_sites():
        if not _is_table_lookup(_raise_arg(node)):
            bad.append(f"{node.lineno}: {ast.unparse(node.exc)[:90]}")
    assert not bad, (
        "text_manager.py 的 raise 实参必须是 `_MSG[\"key\"]`（可 .format(...)）形态。"
        "出现字面量 = 文案又多了一个出处；出现库对象 = 缺陷 39 复发。实际：\n  "
        + "\n  ".join(bad))


def test_l1_no_library_exception_reaches_any_raise_arg():
    """L1 的判据面：实参里不得引用任何 `except ... as E` 的绑定名。

    与上一条是同一事实的两个投影，分开写是为了**失败信息自解释** —— 「引用了 except
    绑定名」直接指向缺陷 39 本体的形状（`f"...{str(e)}"`），比「形态不对」好读。
    """
    offenders = []
    for handler in ast.walk(_TM_TREE):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        for stmt in handler.body:
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Raise) or node.exc is None:
                    continue
                if any(isinstance(n, ast.Name) and n.id == handler.name
                       for n in ast.walk(node.exc)):
                    offenders.append(f"{node.lineno}: {ast.unparse(node.exc)[:90]}")
    assert not offenders, (
        "库的异常对象被格式化进了 raise 实参 —— 缺陷 39 复发。实际：\n  "
        + "\n  ".join(offenders))


# ── L2：键的闭集 ───────────────────────────────────────────────────────────


def test_l2_used_keys_exactly_match_the_table():
    used = _used_keys()
    table = set(TEXT_FAILURE_MESSAGES)
    assert used - table == set(), f"用了表里没有的键：{sorted(used - table)}"
    assert table - used == set(), f"表里有没人用的陈旧键：{sorted(table - used)}"


# ── L4：非空负控 ───────────────────────────────────────────────────────────


def test_l4_scan_is_not_vacuous():
    """防「对空集断言恒真」：扫描范围塌了（改别名、换写法、文件被搬）必须红。

    数的是 **L1 真正判过的 raise 条数**（`_is_table_lookup` 命中数），不是键的个数 ——
    键是去重的（16 条 raise → 12 键），用键数当门会让「扫到 12 条」与「扫到 16 条」
    分不开。
    """
    n = sum(1 for node in _raise_sites() if _is_table_lookup(_raise_arg(node)))
    assert n >= _MIN_TABLE_RAISES, (
        f"L1 只判到 {n} 条表查询形态的 raise，少于预期的 {_MIN_TABLE_RAISES} —— "
        "扫描范围有盲区（别名没解析对 / 文件被搬 / 写法变了），L1/L2 在假绿")


# ── L5：表单 op —— 正文不许走 Form 字段（缺陷 40 commit 二）───────────────
#
# 命题：**表单 op 上，正文只能走文件字段，其余 Form 字段只能是元数据。** 这条命题本来
# 就适用于所有表单 op：starlette 的 `FormParser` 对**任何**文本表单字段都按 1MB **字节**
# 截断，与本仓哪条路由无关 —— 所以覆盖面按事实取（谁声明了表单 content 就是谁），
# 不是「恰好锁住那一条」。旧版只锁一条不是命题窄，是旧判据扫不动别的 —— 它扫 AST 找
# 「默认值是不是名叫 Form 的调用」，只认那一种写法，`Annotated[str, Form()]` /
# `fastapi.Form(...)` / 只有关键字的参数**静默漏过**，且只 glob 一个文件（缺陷 42 第 3 步）。
#
# 覆盖面由 `route_facts.form_operations()` 给，**文件字段与非文件字段的区分由
# `route_facts.is_file_field(schema)` 给** —— 都不写手工清单，也不按字段名猜。手工清单是
# 守卫与被守对象之间的第二份副本：新增一条表单路由不会红、只会假绿；按字段**叫不叫**
# `file` 排除同理 —— 把 `UploadFile = File(...)` 改成 `str = Form(...)` 之后，正文通道
# 事实上已经换了，而判据**全绿**，属于**静默绕过**（缺陷 42 结案：这是「按名字判断身份」
# 的第四次显形，V9/V10 钉住）。判定「哪些 content-type 算表单」在事实层只有一份
# （`_form_media`，`form_operations` 与 `form_fields` 共用）—— 同一判定两份实现会各自
# 漂移，而漂移**不报错**，只让两处对同一条 op 给出不同答案（第 3b 步收掉）。
#
# 降不下去的那一半（AGENTS.md §四③层）：**哪些非文件字段算元数据是策略不是事实** ——
# 「这个字段是不是正文通道」没有事实层对应物（starlette 对所有文本字段一视同仁）。这一层
# 的失效方向是**响亮误伤**（签名多一个字段就红、人来看一眼），不是静默漏过。

# 每个表单 op 上**允许**存在的非文件字段 → 理由。策略表，键是 `(path, method, field)`。
_FORM_METADATA: dict[tuple[str, str, str], str] = {
    ("/api/text/upload", "post", "title"): "文本标题，落库当标签",
    ("/api/text/upload", "post", "description"): "文本描述，落库当标签",
    ("/api/text/upload", "post", "text_type"): "story/classic 分流开关，不承载正文",
    ("/api/voice/upload", "post", "name"): "音色名称，落进音色库当标签",
    ("/api/voice/ref-audio/upload", "post", "card_id"): "卡片主键，参考音频的归属",
    # `ref_text` 是**判断不是事实**：它是参考音频的转写，本仓对它没有长度上限，与缺陷 40
    # 的病灶（starlette 1MB **字节** vs 本仓 100 万**字符**，单位不可换算）不同类。
    ("/api/voice/ref-audio/upload", "post", "ref_text"):
        "参考音频转写，仅作 TTS prompt_text，不进文本库",
    # 只有一个文件字段、没有任何非文件字段的那条 op 不出现在本表里：没有需要登记的字段。
}


def _fmt(keys) -> str:
    """`{('/', 'post', 'x')}` → `('/','post','x')`：紧凑到失败信息里一眼看得见是哪个键。"""
    return "、".join("(" + ",".join(repr(x) for x in k) + ")" for k in sorted(keys))


def _observed_non_file_fields() -> set[tuple[str, str, str]]:
    """全仓表单 op 上**非文件**字段的全集 —— 现场账，与 `_FORM_METADATA` 对账。"""
    return {
        (path, method, name)
        for path, method in route_facts.form_operations()
        for name, schema in route_facts.form_fields(path, method).items()
        if not route_facts.is_file_field(schema)
    }


def test_l5_no_form_op_has_a_payload_channel_besides_the_file_field():
    """主判据：表单 op 上出现的每一个非文件字段，都必须在 `_FORM_METADATA` 里登记过。

    文件字段是**唯一**被允许的正文通道 —— 任何多出来的文本 Form 字段都只能是一条被
    starlette 先用 **1MB 字节**截断的无界通道，而本仓正文上限是 100 万**字**（三字节/字
    ≈ 3MB）。两者单位不可换算，所以在框架之前加门只能做到「更早报一个错」，做不到
    「调用方传不出非法值」（§四）。`/api/text/upload` 的 `text` / `filename` 两个字段
    正是这样被下掉的。

    新增元数据字段（如 `language = Form("")`）会让这条红 —— 那是**故意的**：签名多一个
    Form 字段，就该有人看一眼它是不是正文通道；确认不是，就把它加进 `_FORM_METADATA`
    并注明理由。
    """
    unregistered = policy_table.unexpected(_observed_non_file_fields(), _FORM_METADATA)
    assert not unregistered, (
        f"这些表单 op 上出现了未登记的非文件 Form 字段：{_fmt(unregistered)}。正文只能走文件"
        "字段（`is_file_field` 按 schema 判，不看字段名）—— FormParser 的 1MB 上限"
        "（**字节**）会先于本仓的 100 万字（**字符**）触发，上屏成库的英文文案"
        "`Field exceeded maximum size of 1024KB.`（缺陷 40）。若新字段确实是元数据，"
        "加进本文件 `_FORM_METADATA` 并注明理由。")


def test_l5_metadata_policy_is_neither_stale_nor_reasonless():
    """名单不腐烂：登记过的字段若已消失（或已改名）条目就该删；理由不许是空的。

    与主判据分开写是因为**失效方向不同** —— 主判据红在「现场多了东西」，这条红在
    「表里错了」。两条混在一起时，失败信息说不清该改哪一边。
    """
    observed = _observed_non_file_fields()
    stale = policy_table.stale_keys(_FORM_METADATA, observed)
    assert not stale, (
        "_FORM_METADATA 里的字段已不再出现在任何表单 op 上（路由或字段被删/改名），"
        f"请删除：{_fmt(stale)}")
    blank = policy_table.empty_reasons(_FORM_METADATA)
    assert not blank, (
        f"_FORM_METADATA 里这些键的理由是空的 —— 写了理由不等于理由有内容：{_fmt(blank)}")


def test_l5_scan_is_not_vacuous():
    """负控（数扫描命中的东西）—— 三条都不能只靠口头保证。

    负控塌成空集时主判据会**恒真**：没有表单 op、没有文件字段、或现场一个非文件字段都
    数不出来，`unexpected(...)` 都返回空集 —— 那是本文件 L4 同款的假绿（§四）。
    """
    ops = route_facts.form_operations()
    assert ops, "一条表单 op 都没枚举到 —— 扫描面失效（spec 生成 / content-type 判据坏了）"

    with_file_field = [k for k in ops
                       if any(route_facts.is_file_field(s)
                              for s in route_facts.form_fields(*k).values())]
    assert with_file_field, (
        "没有任何表单 op 带文件字段 —— 「正文只能走文件字段」这条断言失去了对象，是假绿")

    assert _observed_non_file_fields(), (
        "现场一个非文件 Form 字段都数不出来 —— 主判据在对空集断言（假绿）")


# ── L6：解析库的 import 位置 —— 校验失败的路径不得触达解析器（缺陷 41 · ④）────
#
# 命题：**一个已经能失败的函数里，重解析器的 import 不得排在失败点之前。** 判据落在
# **位置**（行号先后），不落在「多重算重」上 —— 于是不需要定义什么叫「重依赖」，也就
# 不需要任何名单。这条命题有生产后果：本仓没有一处 import onnxruntime，是 `pymupdf4llm`
# 在**包 import 期**无条件拉进来的（实测链，钩子取在 DLL 加载之前：
# `pymupdf4llm/helpers/utils.py:6` → `ocr/analyze_page.py:5  import onnxruntime as ort`），
# 而 `_extract_pdf` 的 docstring 自己写着 "Does NOT OCR scanned PDFs" —— **依赖的能力我们
# 明确不用，成本却全付了**：一个打不开的文件，在 `pymupdf.open()` 抛错之前就先付了 40MB
# 推理运行时的加载代价（本机表现为进程级 access violation，try/except 拦不住 —— 那不是
# 「错误」是「进程没了」）。
#
# 两条判据，失效方向不同，分开写（理由同 L5 那两条）：
#   ① 模块顶层不得有第三方 import —— 顶层 import 无条件执行，「校验」还没机会发生。
#   ② 每个函数里**无守卫**的第三方 import 至多一条（无守卫 = 行号早于该函数首条 `raise`）。
#
# ② 为什么是「至多一条」而不是「零条」：**第一条是那个函数自己的工具** ——
# `_read_text_file` 的 `aiofiles`、`_extract_pdf` 的 `pymupdf`、`_extract_docx` 的 `docx`
# 都必须先于该函数的第一次校验，因为在它们之前**没有更早的失败点可比**。要求「零条」会让
# 这三条判别器在任何合法写法下都红不了 —— 那是**死判据**，不是判据（本仓不设豁免名单的
# 道理见 tests/lock_coverage.py）。这条锁真正管的是「第二条及以后」：`_extract_pdf` 里那条
# `pymupdf4llm` 正是唯一一条被管住的。它的失效方向是**响亮误伤**（某函数真需要两件工具
# 才能开始校验就红，人来看一眼），不是静默漏过。
#
# 「哪些算第三方解析库」从事实推出，不写名单：`requirements.txt` 列出的分发包，经
# `importlib.metadata.packages_distributions()` 映射到顶层模块名，与该文件里实际出现的
# 顶层模块名求交。换库、加库、改依赖清单，这条锁自动跟着走。

_DIST_SPLIT = re.compile(r"[<>=!;\[ ]")


def _required_dists() -> set[str]:
    """`requirements.txt` 里的分发包名（小写）。`-r` / `--index-url` 这类开关行排除。"""
    out: set[str] = set()
    for line in (_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        out.add(_DIST_SPLIT.split(line)[0].lower())
    return out


def _third_party(sites: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """`(顶层模块名, 行号)` 里的第三方那些 —— 由「包名→分发包」的映射判定，不是名单。"""
    provided = {
        top
        for top, dists in importlib.metadata.packages_distributions().items()
        if dists and {d.lower() for d in dists} & _required_dists()
    }
    return [(name, line) for name, line in sites if name in provided]


def _import_sites(stmts: list[ast.stmt]) -> list[tuple[str, int]]:
    """一段语句里 import 的 `(顶层模块名, 行号)`。相对 import 是本仓自己的，不算第三方。

    **按行号排序**：`_own_stmts` 是栈式遍历，出的顺序与源码顺序无关。不排的话
    「这个函数的第一件工具」取的是遍历序的第一个 —— 判断随遍历次序漂，②报出来的
    名字也就随机（T-1 变异点名了 `pymupdf` 而不是挪上去的 `pymupdf4llm`）。
    """
    out: list[tuple[str, int]] = []
    for node in stmts:
        if isinstance(node, ast.Import):
            out += [(a.name.split(".")[0], node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node.module.split(".")[0], node.lineno))
    return sorted(out, key=lambda site: site[1])


def _own_stmts(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """函数**自己**那部分的语句（含 `if`/`try` 里的，不下钻嵌套函数 —— 它们各有各的判断）。

    只取 `fn.body` 的直接语句不够：要挪动的那个 import 本来就在 `try:` 里面，
    只看一层会把「挪到函数顶部」这个变异看成**没有 import**。
    """
    out: list[ast.stmt] = []
    stack: list[ast.stmt] = list(fn.body)
    while stack:
        node = stack.pop()
        out.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(child, ast.stmt):
                stack.append(child)
    return out


def _functions() -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(_TM_TREE)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _per_function() -> list[tuple[str, list[tuple[str, int]], int | None]]:
    """每个函数：`(函数名, 它自己那部分的第三方 import, 首条失败点行号)`。

    失败点 = 该函数**自己**的第一条 `raise`；没有 `raise` 的函数取 `None`（它没有失败点，
    因而所有 import 都算无守卫）。
    """
    out = []
    for fn in _functions():
        own = _own_stmts(fn)
        reaches = [n.lineno for n in own if isinstance(n, ast.Raise)]
        out.append((fn.name, _third_party(_import_sites(own)),
                    min(reaches) if reaches else None))
    return out


def test_l6_no_third_party_import_at_module_level():
    """① 顶层 import 无条件执行 —— 校验还没机会发生，代价已经付了。"""
    bad = _third_party(_import_sites(_TM_TREE.body))
    assert not bad, (
        "text_manager.py 模块顶层出现了第三方 import：" + "、".join(
            f"{name}（第 {line} 行）" for name, line in bad)
        + "。顶层 import 在**任何**校验之前执行 —— 解析库必须挪进用到它的那个函数，"
          "且排在那个函数的失败点之后（缺陷 41 · ④）。")


def test_l6_no_second_third_party_import_before_a_failure_point():
    """② 每个函数里无守卫的第三方 import 至多一条 —— 第一条是那个函数自己的工具。

    第二条起就是「校验失败的路径也要付出的代价」，本锁的对象正是它。
    """
    bad = []
    for fn_name, sites, guard in _per_function():
        lead = [(name, line) for name, line in sites if guard is None or line < guard]
        for name, line in lead[1:]:
            bad.append(f"{fn_name} 第 {line} 行 `import {name}`（失败点在第 {guard} 行）")
    assert not bad, (
        "这些第三方 import 排在各自函数的失败点之前、且不是该函数的第一件工具："
        + "；".join(bad)
        + "。把它们挪到校验之后（`_extract_pdf` 里 `pymupdf4llm` 的样子），"
          "或说明为什么它必须在失败点之前。")


def test_l6_scan_is_not_vacuous():
    """负控：判据面塌了（requirements 读不到 / 映射断 / import 扫描瞎）上面两条就恒真。

    真正要守的是**「位置」这条判断有对象**：必须存在「排在失败点之后」的第三方 import
    —— 那正是被修好的那个（`pymupdf4llm`）。它要是又挪回函数顶部，这里当场变空集。
    """
    third = [name for _, sites, _ in _per_function() for name, _ in sites]
    assert third, (
        "一个第三方模块都没认出来 —— 「哪些算解析库」的推导断了（requirements 读不到 / "
        "packages_distributions 映射空），L6 两条都在对空集断言（假绿）")

    after = [name for _, sites, guard in _per_function()
             for name, line in sites if guard is not None and line > guard]
    assert after, (
        "没有任何第三方 import 排在失败点**之后** —— 「位置」这条判断失去了对象，"
        "② 在假绿（解析器又被搬回函数顶部了？）")


# ── L6 的运行时面：钩子断言「没触达」，不是「本机没崩」───────────────────────


class _ParserRuntimeTouched(BaseException):
    """**不是** `Exception` 的子类。

    被判定代码里有宽 `except Exception`（`_extract_pdf` 里 `to_markdown` 那圈会抓走
    Exception 再包成 `pdf_parse_failed`，`pymupdf.open` 那圈包成 `pdf_open_failed`）——
    若钩子抛的是 Exception 子类，它会被抓走再包一层，于是**钩子响了而判据仍然是绿的**。
    """


def test_l6_bad_pdf_does_not_touch_the_parser_runtime(monkeypatch):
    """不变量：**校验失败的路径不得触达解析器** —— 用 import 钩子断言「没触达」。

    **不许拿「本机不崩了」当证据**：那在装了能跑的 onnxruntime 的机器上恒真（容器里
    1068 passed 正是这形状），是假绿。钩子只认「这次 import 有没有发生」，与机器无关。

    前提先断言：本进程此前没加载过 `pymupdf4llm` / `onnxruntime` —— 已加载的话
    `sys.modules` 会短路，`meta_path` 根本不被问到，这条就只能证明「早就加载过」，
    对「本次有没有触达」无从判定。
    """
    assert "pymupdf4llm" not in sys.modules and "onnxruntime" not in sys.modules, (
        "跑这条判据之前进程里已经加载了解析器运行时（pymupdf4llm / onnxruntime）——"
        "`sys.modules` 会短路、钩子不会被问到，本条无从判定：先查是谁提前拉了它")

    touched: list[str] = []

    class _Tripwire:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] == "onnxruntime":
                touched.append(fullname)
                raise _ParserRuntimeTouched(fullname)
            return None

    wire = _Tripwire()
    sys.meta_path.insert(0, wire)
    try:
        r = _upload_file(monkeypatch, "bad.pdf", b"not a pdf at all")
    finally:
        sys.meta_path.remove(wire)

    assert not touched, (
        f"非法 PDF 的失败路径触达了 {touched} —— 校验失败不该付出加载 40MB 解析器的代价"
        "（`pymupdf4llm` 在包 import 期无条件拉进 onnxruntime）。把那条 import 挪到"
        "`pymupdf.open()` 与页数校验之后（L6）。")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == TEXT_FAILURE_MESSAGES["pdf_open_failed"]


# ── L3：端到端 —— 上屏是表里那句，线索在日志里 ─────────────────────────────


class _NullStore:
    async def save_text(self, *a, **kw) -> None:  # pragma: no cover - 三例都在落盘前失败
        raise AssertionError("三例都该在保存之前失败")


def _client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter          # text 路由带 @limiter.limit
    app.include_router(text_router)
    app.dependency_overrides[get_storage] = lambda: _NullStore()
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "t", "is_admin": False}

    async def _fake_user_llm(*a, **kw):
        return object()          # 非 None，否则路由先 503

    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(
        deps, "get_text_manager",
        lambda *a, **kw: TextManager(_NullStore(), None, None, {}, {}),
    )
    return TestClient(app, raise_server_exceptions=False)


def _upload_file(monkeypatch, name: str, content: bytes):
    return _client(monkeypatch).post(
        "/api/text/upload",
        data={"text_type": "other"},     # 非 story/classic，免得成功后起后台线程
        files={"file": (name, content, "application/octet-stream")},
    )


def test_l3_bad_pdf_screens_table_wording_and_logs_the_original(monkeypatch, capsys):
    r = _upload_file(monkeypatch, "bad.pdf", b"not a pdf at all")

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail == TEXT_FAILURE_MESSAGES["pdf_open_failed"], detail
    for leak in ("Failed to open file", "pymupdf", "Traceback", "tmp", "\\"):
        assert leak not in detail, f"库原文漏上屏：{detail!r}"

    out = capsys.readouterr().out
    assert "PDF open failed" in out, f"原始诊断被收走却没进日志 = 从「泄漏」换成「瞎」：{out!r}"
    assert "Failed to open file" in out, out


def test_l3_empty_docx_is_not_double_wrapped(monkeypatch, capsys):
    """DOCX 双包回归：本仓自己的判定不该被同一圈 `except Exception` 再包一层。"""
    with tempfile.TemporaryDirectory() as d:
        from docx import Document
        p = os.path.join(d, "empty.docx")
        Document().save(p)
        payload = pathlib.Path(p).read_bytes()

    r = _upload_file(monkeypatch, "empty.docx", payload)

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail == TEXT_FAILURE_MESSAGES["docx_empty"], (
        f"双包复发（上屏该只有本仓那一句）：{detail!r}")
    assert "解析失败" not in detail, detail


def test_l3_oversized_text_screens_table_wording(monkeypatch):
    """超长的**唯一**载体是 `file`（缺陷 40 commit 二 把 urlencoded 的 `text` 字段下掉了）。

    载体从 `data={"text": …}` 换成文件上传**不是**为了绕开 starlette 的 1MB 上限，而是
    因为那条通道已经不存在了 —— 命题（超长 → 400 + 表里那句）原样保留。带 filename 的
    part 不受 `max_part_size` 约束（starlette 的检查写在 `if self._current_part.file is None:`
    里面），所以 100 万零 1 字（≈3MB）能走到本仓自己的 `max_chars`。
    """
    r = _upload_file(monkeypatch, "big.txt", ("字" * 1_000_001).encode("utf-8"))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == TEXT_FAILURE_MESSAGES["too_long"].format(limit_text="100 万")


def _string_literals(tree: ast.Module) -> set[str]:
    """模块里所有字符串字面量的值。

    **注释不进 AST** —— 这正是本锁要从「原始源文本子串」改成 AST 常量的原因：
    `_extract_docx` 那边有一句注释原样引用了双包上屏串来说明成因，它到不了用户
    眼前，拿它当「第二个出处」是把注释和代码混为一谈。
    """
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_l3_wording_has_a_single_source():
    """同一句话只有一个出处：表里那句不该作为**字面量**在 `text_manager.py` 里再出现。"""
    literals = _string_literals(_TM_TREE)
    for key, msg in TEXT_FAILURE_MESSAGES.items():
        if "{" in msg:            # 模板不逐字比对，只查无占位符的那些
            continue
        assert msg not in literals, f"表键 {key!r} 的文案在 text_manager.py 里又抄了一份"


@pytest.mark.parametrize("key", sorted(TEXT_FAILURE_MESSAGES))
def test_table_entries_are_nonempty_prose(key):
    assert TEXT_FAILURE_MESSAGES[key].strip(), f"{key} 是空文案"
