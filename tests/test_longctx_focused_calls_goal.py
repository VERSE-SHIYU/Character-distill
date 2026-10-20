# -*- coding: utf-8 -*-
"""S1 C1–C2：按组格式化只留一处实现，且 F1 的行为逐字不变。

C1 是目标检查（实现前 `xfail(strict=True)`）：组循环与 `PHASE_DEPENDENT_GROUPS` 的
消费点只许出现在新抽出的共用方法里。

C2 是回归护栏（基线本来就绿）：用替身 LLM 跑 F1，把每次组调用的 `(system, messages)`
与 formatting 之后的帧序列跟改前录下的 fixture 逐字比较。fixture 由基线
`9200a502` 的同一段代码录制（`e2e/scratch/record_f1_calls.py`，不入库），期望值在
第一个提交里随测试一起入库。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import core.distiller as D
from core.card_draft import CardDraft
from core.distiller import Distiller
from core.schema import FORMAT_GROUPS

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = json.loads(
    (_ROOT / "tests" / "fixtures" / "longctx_focused_calls_f1_baseline.json")
    .read_text(encoding="utf-8"))

#: 段 1 抽出的共用方法名（spec §3）。
_SHARED_METHOD = "_format_groups"

_TEXT = "AB" * 5000
_NAME = "AB"
_USAGE = {"prompt_tokens": 1, "completion_tokens": 1}

_ARC = {"axis": "从甲到乙", "phases": [
    {"label": "阶段一", "state": "开头时的状态", "anchor": ""},
    {"label": "阶段二", "state": "后来的状态", "anchor": ""},
]}


def _function_owners(name: str, *, loops_only: bool = False) -> set[str]:
    """生产代码里引用 `name` 的**最外层函数名**集合（嵌套闭包算进它所在的方法）。

    `loops_only=True` 只数 `for … in name` 的组循环；否则数全部 Name/Attribute。
    只看 AST，不看注释与文档串 —— 规则说的是「消费点」。
    """
    tree = ast.parse((_ROOT / "core" / "distiller.py").read_text(encoding="utf-8"))
    owners: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def _note(self) -> None:
            if self.stack:
                owners.add(self.stack[0])

        def visit_Name(self, node: ast.Name) -> None:
            if not loops_only and node.id == name:
                self._note()
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if (not loops_only and isinstance(node.value, ast.Name)
                    and node.value.id == name):
                self._note()
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> None:
            if loops_only and isinstance(node.iter, ast.Name) and node.iter.id == name:
                self._note()
            self.generic_visit(node)

    _Visitor().visit(tree)
    return owners


def test_c1_grouped_format_has_one_implementation():
    assert _function_owners("PHASE_DEPENDENT_GROUPS") == {_SHARED_METHOD}
    assert _function_owners("FORMAT_GROUPS", loops_only=True) == {_SHARED_METHOD}


def _payload_for(group: str) -> dict:
    base = CardDraft(name=_NAME).model_dump()
    if group == "G6":
        base["character_arc"] = _ARC
        base["situation_behaviors"] = []
        base["key_memories"] = []
    if group == "G5":
        base["relationships"] = []
    return {k: base[k] for k in FORMAT_GROUPS[group]}


def _run_f1() -> tuple[dict[str, dict], list[str], dict]:
    """跑一次真实的 F1 分组·流式路径；替身按组原样回包，不改写收到的内容。"""
    calls: dict[str, dict] = {}

    class _Client:
        async def close(self):
            pass

    class _LLM:
        last_usage = None
        model = "m"

        def _make_async_client(self):
            return _Client()

        async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
            return ("片段分析", _USAGE)

        def chat_stream_long(self, system, messages, max_tokens=None, **kw):
            if "你正在整合关于" in system:
                yield "合并结果"
                return _USAGE
            if "只产出这几个人物与主角的关系" in system:
                yield "[]"
                return _USAGE
            group = re.search(r"CharacterCard\[(G\d)\]", system).group(1)
            calls[group] = {"system": system, "messages": messages}
            yield json.dumps(_payload_for(group), ensure_ascii=False)
            return _USAGE

    d = Distiller(llm=_LLM(), config_path=None)
    d._longctx_threshold = 0
    d._chunk_size = 3000
    frames = list(d.distill_incremental_stream(
        _TEXT, _NAME, aliases=[], text_type="story"))

    start = next(i for i, f in enumerate(frames)
                 if isinstance(f, dict) and f.get("status") == "formatting")
    tail = frames[start:]
    kinds: list[str] = []
    for frame in tail:
        if isinstance(frame, str):
            kinds.append("draft")
        elif frame.get("status") == "formatting":
            kinds.append("formatting")
        elif frame.get("heartbeat"):
            kinds.append("heartbeat")
        else:
            kinds.append("other:" + json.dumps(frame, ensure_ascii=False))
    drafts = [json.loads(f) for f in tail if isinstance(f, str)]
    assert len(drafts) == 1, f"成品草稿帧不是恰 1 个：{kinds}"
    return calls, kinds, drafts[0]


def test_c2_f1_calls_and_frames_unchanged(monkeypatch):
    monkeypatch.setattr(D, "try_record_usage", lambda *a, **kw: None)
    calls, kinds, draft = _run_f1()

    assert sorted(calls) == sorted(FORMAT_GROUPS)
    for group in FORMAT_GROUPS:
        assert calls[group] == _FIXTURE["group_calls"][group], group
    assert kinds == _FIXTURE["tail_frame_kinds"]
    assert draft == _FIXTURE["draft"]
