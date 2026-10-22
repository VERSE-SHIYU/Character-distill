# -*- coding: utf-8 -*-
"""S1 C1–C2：按组格式化只留一处实现，且 F1 的行为逐字不变。
S2 G1–G4：一次读完路径改为按组专注调用，关系补全收回 distiller。

C1 是目标检查（实现前 `xfail(strict=True)`）：组循环与 `PHASE_DEPENDENT_GROUPS` 的
消费点只许出现在新抽出的共用方法里。

C2 是回归护栏（基线本来就绿）：用替身 LLM 跑 F1，把每次组调用的 `(system, messages)`
与 formatting 之后的帧序列跟改前录下的 fixture 逐字比较。fixture 由基线
`9200a502` 的同一段代码录制（`e2e/scratch/record_f1_calls.py`，不入库），期望值在
第一个提交里随测试一起入库。段 2 有意改变 F1（G6 拆分、弧线先单发），fixture 在
实现提交里按新期望重录（spec §8.4/§8.6），`baseline_commit` 记重录所用的提交（见 fixture 的同名字段）。

G1–G4 是段 2 的目标检查，实现前全部 `xfail(strict=True)`（spec §8.4）：
G1 一次读完每次调用以整书前缀开头、弧线组先单独一次、其余组在它之后并行、
依赖组带阶段列表；G2 分组与阶段依赖；G3 所有路径交出的草稿关系已补全、不再由
调用方补；G4 任一组失败整步失败（弧线组、其余组两个出口各测一次）；G5 后台 /start
的进度在格式化阶段按组推进（spec §8.11 复审补充）。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

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
    {"label": "阶段二", "state": "后来的状态", "anchor": "", "key_moments": ["转折发生的那件事"]},
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


def _payload_for(group: str, relationships: tuple[str, ...] = ()) -> dict:
    base = CardDraft(name=_NAME).model_dump()
    if group == "G6":       # 段 2 起弧线单独成组（spec §8.2）
        base["character_arc"] = _ARC
    if group == "G7":
        base["situation_behaviors"] = []
        base["key_memories"] = []
    if group == "G5":
        base["relationships"] = [{"target": t} for t in relationships]
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


# ── 段 2 目标检查（G1–G4，spec §8.4）─────────────────────────────────────

_TEXT_S2 = "开头甲甲甲。中间乙乙乙。"
_REL_TARGET = "某人"
_REL_MARKER = "只产出这几个人物与主角的关系"


def _group_llm(*, bad_group: str | None = None, relationships: tuple[str, ...] = ()):
    """组感知替身：按 `CharacterCard[Gn]` 回该组字段；一次读完主调用回整张卡。

    照生产行为写（返工经验第 35 条）：不改写 system、不改写回包；关系分批回详情。
    `events` 记 `start:<组>` / `end:<组>`，用来断言「弧线组先返回、其余组在它之后」。
    """
    calls: list[dict] = []
    events: list[str] = []

    class _Client:
        async def close(self):
            pass

    class _LLM:
        last_usage = None
        model = "stub"

        def _make_async_client(self):
            return _Client()

        async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
            return ("片段分析", _USAGE)

        def chat(self, system, messages, max_tokens=None, **kw):
            # 基线的一次读完 / 收尾格式化主调用：交一张完整卡
            return json.dumps(CardDraft(name=_NAME).model_dump(), ensure_ascii=False)

        def chat_stream_long(self, system, messages, max_tokens=None, **kw):
            if _REL_MARKER in system:
                yield json.dumps([
                    {"target": t, "relation": "朋友", "attitude": "亲近", "note": "",
                     "attitudes": []} for t in relationships], ensure_ascii=False)
                return _USAGE
            match = re.search(r"CharacterCard\[(G\d)\]", system)
            if match is None:
                # 基线的一次读完主调用没有组标记（段 2 实现后不会再走到）
                yield json.dumps(CardDraft(name=_NAME).model_dump(), ensure_ascii=False)
                return _USAGE
            group = match.group(1)
            events.append("start:" + group)
            calls.append({"group": group, "system": system, "messages": messages,
                          "max_tokens": max_tokens})
            if group == bad_group:
                # 合法 JSON 但缺该组必需字段：重修环的 Attempt 2 会带着原 system 重问，
                # 三次尝试都失败才算「这一组失败」（生产语义）。
                yield "{}"
            else:
                yield json.dumps(_payload_for(group, relationships), ensure_ascii=False)
            events.append("end:" + group)
            return _USAGE

    return _LLM(), calls, events


def _run_f3(llm, text: str = _TEXT_S2, name: str = _NAME):
    return Distiller(llm=llm, config_path=None)._distill_longcontext(text, name)


def _run_f4(llm, text: str = _TEXT_S2, name: str = _NAME) -> list:
    return list(Distiller(llm=llm, config_path=None)._distill_longcontext_stream(text, name))


@pytest.mark.parametrize("path", ["f3", "f4"])
def test_g1_one_pass_calls_are_grouped_arc_first(path: str):
    from core.distiller import PHASE_DEPENDENT_GROUPS, _phase_list_note, book_prefix

    llm, calls, events = _group_llm()
    if path == "f3":
        _run_f3(llm)
    else:
        _run_f4(llm)

    groups = [c["group"] for c in calls]
    assert groups.count("G6") == 1, f"弧线组不是恰一次：{groups}"
    assert groups[0] == "G6", f"第一次调用不是弧线组：{groups}"
    assert sorted(groups) == sorted(FORMAT_GROUPS), f"不是每组各一次：{groups}"

    note = _phase_list_note(_ARC["phases"])
    for call in calls:
        system = call["system"]
        assert system.startswith(book_prefix(_TEXT_S2)), f"{call['group']} 的 system 不是整书前缀开头"
        assert call["max_tokens"] == Distiller.CARD_MAX_TOKENS, call["group"]
        assert _TEXT_S2 not in "".join(m["content"] for m in call["messages"]), \
            f"{call['group']} 把全文又塞进 user 消息"
        if call["group"] == "G6":
            assert "L. 角色弧线" in system
            assert "E. 关键记忆" not in system and "O. 情境→行为" not in system, \
                "弧线组不是只含弧线模板"
        if call["group"] in PHASE_DEPENDENT_GROUPS:
            assert note in system, f"{call['group']} 的 system 没带阶段列表"
        else:
            assert note not in system, f"{call['group']} 不该带阶段列表"

    end_g6 = events.index("end:G6")
    assert all(events.index("start:" + g) > end_g6 for g in groups[1:]), \
        f"有组在弧线组返回前就启动了：{events}"


def test_g2_arc_group_split_and_phase_dependency_derived():
    from core.distiller import PHASE_DEPENDENT_GROUPS
    from core.schema import POST_FORMAT_FIELDS, CharacterCard

    assert FORMAT_GROUPS["G6"] == ("character_arc",)
    assert FORMAT_GROUPS["G7"] == ("situation_behaviors", "key_memories")
    assert "G7" in PHASE_DEPENDENT_GROUPS

    buckets = [set(f) for f in FORMAT_GROUPS.values()] + [set(POST_FORMAT_FIELDS)]
    assert set().union(*buckets) == set(CharacterCard.model_fields)
    assert sum(map(len, buckets)) == len(set().union(*buckets)), "组字段两两有交集"


def test_g3_every_path_hands_over_completed_relationships(monkeypatch):
    monkeypatch.setattr(D, "try_record_usage", lambda *a, **kw: None)
    relationships = (_REL_TARGET,)

    llm, _, _ = _group_llm(relationships=relationships)
    card = _run_f3(llm)
    assert [(r.target, r.relation) for r in card.relationships] == [(_REL_TARGET, "朋友")], \
        "F3 交出的卡关系没补全"

    llm, _, _ = _group_llm(relationships=relationships)
    drafts = [json.loads(f) for f in _run_f4(llm) if isinstance(f, str)]
    assert len(drafts) == 1, drafts
    assert [r.get("relation") for r in drafts[0]["relationships"]] == ["朋友"], \
        "F4 交出的草稿关系没补全"

    llm, _, _ = _group_llm(relationships=relationships)
    d = Distiller(llm=llm, config_path=None)
    d._longctx_threshold = 1
    d._chunk_size = 200
    card = d.distill_incremental(_TEXT_S2 * 10, _NAME, text_type="story")
    assert [(r.target, r.relation) for r in card.relationships] == [(_REL_TARGET, "朋友")], \
        "F2 交出的卡关系没补全"

    assert not hasattr(Distiller, "fill_relationships"), "fill_relationships 仍在"

    # 路由不补关系：按职责查，不按名字查（复审发现 2）。
    # ① 行为：后台 /start 走一次读完，关系分批只由 distiller 自己调一次。
    import web.routers.distill as R
    monkeypatch.setattr(R, "_set_task", lambda *_a, **_kw: None)
    llm, _, _ = _group_llm(relationships=relationships)
    d = Distiller(llm=llm, config_path=None)
    batched_calls: list[int] = []
    real_batched = d._relationships_batched
    monkeypatch.setattr(d, "_relationships_batched",
                        lambda *a, **kw: batched_calls.append(1) or real_batched(*a, **kw))
    assert R._distill_to_draft("t-g3", _NAME, _TEXT_S2, "story", [], None, d) is not None
    assert len(batched_calls) == 1, f"关系分批不是只由 distiller 调一次：{len(batched_calls)}"
    # ② 源码：SSE 那一处没有前端调用方、不好跑行为，查路由里不出现任何补关系的入口
    router = (_ROOT / "web" / "routers" / "distill.py").read_text(encoding="utf-8")
    for name in ("fill_relationships", "_relationships_batched", "relationship_batch"):
        assert name not in router, f"路由仍在补关系：{name}"


# 失败有两个出口：弧线组单独先发、单独取结果（`_format_groups` 第一次 `fmt_queue.get()`），
# 其余组在后面的循环里取。两个出口各测一次，一个都不能漏（复审发现 1）。
@pytest.mark.parametrize("bad_group", ["G6", "G4"])
def test_g4_any_group_failure_fails_the_whole_step(monkeypatch, bad_group: str):
    from core.distiller import DistillError

    monkeypatch.setattr(D, "try_record_usage", lambda *a, **kw: None)

    llm, calls, _ = _group_llm(bad_group=bad_group)
    with pytest.raises(DistillError):
        _run_f3(llm)
    if bad_group == "G6":
        assert {c["group"] for c in calls} == {"G6"}, \
            f"弧线组失败后仍发了其余组：{[c['group'] for c in calls]}"

    llm, _, _ = _group_llm(bad_group=bad_group)
    frames = _run_f4(llm)
    errors = [f for f in frames if isinstance(f, dict) and "error" in f]
    assert len(errors) == 1, f"流式不是恰一个 error 帧：{frames}"
    assert not [f for f in frames if isinstance(f, str)], "组失败还交出了草稿"


def test_g5_start_progress_advances_per_group(monkeypatch):
    """后台 `/start` 走一次读完时，格式化阶段的进度按组推进，不停在 40%（复审发现 4）。

    改前进度按流式 token 的长度算；段 2 起 F4 只在最后交一个草稿串，旧算法退化成
    「停在 40% 到最后一跳」。前端 `useAppStore.js` 读 `progress_pct`，这是用户看得见的。
    """
    import web.routers.distill as R

    monkeypatch.setattr(D, "try_record_usage", lambda *a, **kw: None)
    updates: list[dict] = []
    monkeypatch.setattr(R, "_set_task", lambda _tid, u: updates.append(dict(u)))

    llm, _, _ = _group_llm()
    d = Distiller(llm=llm, config_path=None)
    assert d._takes_one_pass(_TEXT_S2), "前提：这段文本走一次读完（F4）"
    data = R._distill_to_draft("t-progress", _NAME, _TEXT_S2, "story", [], None, d)
    assert data is not None, f"草稿没交出来：{updates}"

    pcts = [u["progress_pct"] for u in updates if "progress_pct" in u]
    start = pcts.index(40)
    fmt = pcts[start:]
    assert len(fmt) == len(FORMAT_GROUPS) + 1, f"不是每组推进一次：{fmt}"
    assert fmt == sorted(fmt) and len(set(fmt)) == len(fmt), f"进度没有逐组上升：{fmt}"
    assert fmt[-1] == 95, f"格式化结束时没到 95%：{fmt}"


def test_g5_formatting_heartbeat_without_progress_is_ignored(monkeypatch):
    """格式化心跳不带组进度（缺 `total` 或为 0）时：照常完成，进度停在 40%，不报错。

    生产里格式化心跳只由 `_format_groups` 发、总带 `total`；这里钉住路由对其余形态的
    口径（跳过，不除零、不中断任务），防止有人把它改成「缺进度就报错」（复审第二轮发现 1）。
    """
    import web.routers.distill as R

    updates: list[dict] = []
    monkeypatch.setattr(R, "_set_task", lambda _tid, u: updates.append(dict(u)))

    class _Stub:
        def distill_incremental_stream(self, *_a, **_kw):
            yield {"status": "formatting"}
            yield {"heartbeat": True}
            yield {"heartbeat": True, "current": 1, "total": 0}
            yield json.dumps({"name": _NAME}, ensure_ascii=False)

    data = R._distill_to_draft("t-hb", _NAME, _TEXT_S2, "story", [], None, _Stub())
    assert data == {"name": _NAME}, f"任务没有照常完成：{updates}"
    assert not [u for u in updates if u.get("status") == "error"], updates
    assert [u["progress_pct"] for u in updates if "progress_pct" in u] == [40], updates
