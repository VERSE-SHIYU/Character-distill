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
     operation，正文只能走**文件字段**（由 schema 判定，不看字段名），其余 Form 字段只能
     是元数据（缺陷 40 commit 二；缺陷 42 结案改判据）。

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
import route_policy
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
    unregistered = route_policy.unexpected(_observed_non_file_fields(), _FORM_METADATA)
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
    stale = route_policy.stale_keys(_FORM_METADATA, observed)
    assert not stale, (
        "_FORM_METADATA 里的字段已不再出现在任何表单 op 上（路由或字段被删/改名），"
        f"请删除：{_fmt(stale)}")
    blank = route_policy.empty_reasons(_FORM_METADATA)
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
