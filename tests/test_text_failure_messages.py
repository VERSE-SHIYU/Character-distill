# -*- coding: utf-8 -*-
"""缺陷 39：文本解析层不再负责用户文案 —— 抛「发生了什么」（一个键），文案归表。

根因不是「顺手多拼了个 `{str(e)}`」，是分层坏了：`text_manager.py` 既要解析文件、
又要决定用户看到什么话，于是同一个 `except` 同时接住「我们写的用户文案」和「库的异常
原文」（DOCX 双包：上屏成了 `"DOCX 解析失败: DOCX 文件无有效文本内容"`）。

本文件锁四件事（判据驱动，**不维护点位白名单** —— 判据从 `raise` 实参的形态直接推出）：

  L1 实参形态（静态 AST）：`text_manager.py` 每条 `raise` 的实参必须是 `_MSG[...]`
     或 `_MSG[...].format(...)` —— 里面不得出现字符串字面量，也不得引用任何 `except`
     绑定名。这条同时堵住「库原文回到上屏串」和「文案在别处又抄一份」。
  L2 闭集：用到的键 == 表键（漏一个红、表里有陈旧项也红）。
  L3 端到端 + 线索不丢：坏 PDF / 空 DOCX / 超长走 `/api/text/upload` → 400 + `detail`
     **恰等于**表里那句、不含路径与库名；**同时**日志里必须有原始诊断（收走原文 ≠
     扔掉原文 —— 那是把「泄漏」换成「瞎」）。
  L4 非空负控：L1 的扫描必须命中 ≥ 16 条 `_MSG[...]` 形态的 raise。防「对空集断言
     恒真」的假绿（§四：凡「所有 X 都满足 P」的断言，先问 X 会不会是空集）。

为什么 L1 的别名从 import 语句解析、不写成常量：写常量就是「守卫与被守对象之间的
第二份手工清单」—— 改别名时锁不会红，只会变成假绿。
"""
from __future__ import annotations

import ast
import os
import pathlib
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "缺陷 40：starlette 1.6.0 给 urlencoded 的 `FormParser` 也加了 1MB 逐字段上限"
        "（1.0.0 只有 `MultiPartParser` 有），它先于本仓的 `max_chars` 触发，上屏成了"
        "库的 `Field exceeded maximum size of 1024KB.` —— 本仓文案根本没机会出现。"
        "commit 二 要么下掉 `text` 字段、要么把长度判断前移到框架之前，届时本用例转绿。"
        "**`strict=True` 是承重的**：它把「什么都没修」和「修好了」变成两个信号 —— "
        "commit 二 落地后这句 xfail 会当场 XPASS 成红，逼着删掉它，而不是留一个腐烂的"
        "「永远 xfail」把真实回归一起吞掉。同一条 strict 也让把锁退回旧 starlette 这件事"
        "当场变红（旧版没有这个上限，用例会通过）。"
    ),
)
def test_l3_oversized_text_screens_table_wording(monkeypatch):
    r = _client(monkeypatch).post(
        "/api/text/upload",
        data={"text": "字" * 1_000_001, "filename": "big.txt", "text_type": "other"},
    )
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
