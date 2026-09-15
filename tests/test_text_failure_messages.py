# -*- coding: utf-8 -*-
"""缺陷 39：文本解析层不再负责用户文案 —— 抛「发生了什么」（一个键），文案归表。

根因不是「顺手多拼了个 `{str(e)}`」，是分层坏了：`text_manager.py` 既要解析文件、
又要决定用户看到什么话，于是同一个 `except` 同时接住「我们写的用户文案」和「库的异常
原文」（DOCX 双包：上屏成了 `"DOCX 解析失败: DOCX 文件无有效文本内容"`）。

本文件锁五件事（判据驱动，**不维护点位白名单** —— 判据从 `raise` 实参的形态直接推出）：

  L1 实参形态（静态 AST）：`text_manager.py` 每条 `raise` 的实参必须是 `_MSG[...]`
     或 `_MSG[...].format(...)` —— 里面不得出现字符串字面量，也不得引用任何 `except`
     绑定名。这条同时堵住「库原文回到上屏串」和「文案在别处又抄一份」。
  L2 闭集：用到的键 == 表键（漏一个红、表里有陈旧项也红）。
  L3 端到端 + 线索不丢：坏 PDF / 空 DOCX / 超长走 `/api/text/upload` → 400 + `detail`
     **恰等于**表里那句、不含路径与库名；**同时**日志里必须有原始诊断（收走原文 ≠
     扔掉原文 —— 那是把「泄漏」换成「瞎」）。
  L4 非空负控：L1 的扫描必须命中 ≥ 16 条 `_MSG[...]` 形态的 raise。防「对空集断言
     恒真」的假绿（§四：凡「所有 X 都满足 P」的断言，先问 X 会不会是空集）。
  L5 表单 op（读框架自己的账本，**不写手工清单**）：全仓每一条声明了表单 content 的
     operation，正文只能走 `file`，其余 Form 字段只能是元数据（缺陷 40 commit 二）。

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
# 命题：**表单 op 上，正文只能走 `file`，其余 Form 字段只能是元数据。** 这条命题本来
# 就适用于所有表单 op；旧版只锁 `/api/text/upload` 不是命题窄，是旧判据扫不动别的 ——
# 它扫 AST 找「默认值是不是名叫 Form 的调用」，只认那一种写法，`Annotated[str, Form()]` /
# `fastapi.Form(...)` / 只有关键字的参数**静默漏过**，且只 glob 一个文件（缺陷 42）。
# 改读框架自己的账本（`route_facts.form_fields` ⇒ OpenAPI 文档）后，覆盖四条是
# **能力达到**，不是扩面。
#
# 覆盖面由 `route_facts.form_operations()` 给（谁声明了表单 content 就是谁），不写手工
# 清单 —— 清单是守卫与被守对象之间的第二份副本，新增一条表单路由不会红、只会假绿。
# 判定「哪些 content-type 算表单」在事实层只有一份（`_form_media`，`form_operations` 与
# `form_fields` 共用）：本文件原先自己读 `route_facts._FORM_CONTENT_TYPES` / `_METHODS`
# 重算了一遍 —— 跨层读私有常量，且同一判定两份实现。两份会各自漂移，而漂移**不报错**，
# 只让两处对同一条 op 给出不同答案（缺陷 42 第 3b 步收掉）。
#
# 降不下去的那一半（AGENTS.md §四③层）：**哪个字段算元数据、哪个字段是唯一正文通道，
# 是策略不是事实** —— 任何 Form 字段在 starlette 下都同样受 1MB 字节截断，「是不是正文
# 通道」是语义判断，没有事实层对应物。这一层的失效方向是**响亮误伤**（签名多一个字段
# 就红、人来看一眼），不是静默漏过，故不需要绕过型变异。

# 正文通道的字段名：表单 op 上唯一被允许承载正文的字段。
_PAYLOAD_FIELD = "file"

# 每个表单 op 上除正文通道外**允许**出现的字段。策略，不是事实 —— 见上面③层那段。
_FORM_METADATA = {
    ("/api/text/upload", "post"): {"title", "description", "text_type"},
    ("/api/voice/upload", "post"): {"name"},              # 音色名称，落进音色库当标签
    ("/api/voice/ref-audio/upload", "post"): {"card_id", "ref_text"},
    # ↑ `ref_text` 是**判断不是事实**：它是参考音频的转写，本仓对它没有长度上限，与
    #   缺陷 40 的病灶（starlette 1MB **字节** vs 本仓 100 万**字符**，单位不可换算）
    #   不同类。若将来裁定它算正文通道，删掉这里那个键，该 op 立刻红。
    ("/api/voice/asr", "post"): set(),                    # 一个 Form 字段都没有 = 理想形态
}


def _extra_form_fields(path: str, method: str) -> list[str]:
    """该 op 上除正文通道与已声明元数据之外的 Form 字段（`[]` = 合规）。"""
    fields = set(route_facts.form_fields(path, method))
    return sorted(fields - {_PAYLOAD_FIELD} - _FORM_METADATA.get((path, method), set()))


def test_l5_no_form_op_has_a_payload_channel_besides_file():
    """全仓每一条表单 op：正文只能走 `file`。

    任何多出来的 Form 字段都只能是一条新的、由 starlette `FormParser` 先用 **1MB 字节**
    截断的无界通道 —— 而本仓正文的上限是 100 万**字**（三字节/字 ≈ 3MB）。两者单位不可
    换算，所以在框架之前加门只能做到「更早报一个错」，做不到「调用方传不出非法值」（§四）。
    `/api/text/upload` 的 `text` / `filename` 两个字段正是这样被下掉的。

    新增元数据字段（如 `language = Form("")`）会让一条红 —— 那是**故意的**：签名多一个
    Form 字段，就该有人看一眼它是不是正文通道；确认不是，就把它加进 `_FORM_METADATA`
    并注明理由。
    """
    offenders = {k: v for k, v in
                 ((key, _extra_form_fields(*key)) for key in sorted(route_facts.form_operations())) if v}
    assert not offenders, (
        f"这些表单 op 上多出了非正文通道的 Form 字段：{offenders}。正文只能走 "
        f"{_PAYLOAD_FIELD!r} —— FormParser 的 1MB 上限（**字节**）会先于本仓的 100 万字"
        "（**字符**）触发，上屏成库的英文文案 `Field exceeded maximum size of 1024KB.`"
        "（缺陷 40）。若新字段确实是元数据，加进本文件 `_FORM_METADATA` 并注明理由。")


def test_l5_scan_is_not_vacuous_and_policy_has_no_stale_entries():
    """负控（数扫描命中的东西）＋ 名单不腐烂。两条都不能只靠口头保证。

    负控塌成空集时「所有表单 op 都只有 file」会**恒真** —— 那是本文件 L4 同款的假绿。
    所以数两样：枚举到的表单 op 数，以及「正文通道」这个字段真的存在。反向那条防
    `_FORM_METADATA` 留下已消失的 op / 已改名的路由。
    """
    ops = route_facts.form_operations()
    assert ops, "一条表单 op 都没枚举到 —— 扫描面失效（spec 生成 / content-type 判据坏了）"

    with_payload = {k for k in ops if _PAYLOAD_FIELD in route_facts.form_fields(*k)}
    assert with_payload, (
        f"没有任何表单 op 带 {_PAYLOAD_FIELD!r} 字段 —— 「正文只能走 file」这条断言"
        "失去了对象，是假绿")

    stale = set(_FORM_METADATA) - ops
    assert not stale, (
        f"_FORM_METADATA 里的 op 已不在表单 op 集合里（路由被删或改名），请删除：{sorted(stale)}")


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
