# -*- coding: utf-8 -*-
"""目标检查 T1–T6（spec `docs/specs/replay-distill.md` §7）。

第一个提交全部 `xfail(strict=True)`：测的是「工具达成目标」，不是「符合实现」。
实现提交里删掉模块级 xfail，让它们转绿；strict 保证「实现后忘了删」当场红。

替身照生产行为写（返工经验第 35 条）：复用
`tests/test_longctx_focused_calls_goal.py::_group_llm`（按组回包、流有终点），
只在其上覆盖弧线组与做法/记忆组的内容；不复制组协议。
"""
from __future__ import annotations

import asyncio
import json
import re
import types
import uuid
from pathlib import Path

import pytest

from conftest import TEST_DATABASE_URL  # noqa: E402
from adapters.llm_adapter import IncompleteResponseError  # noqa: E402
from core import phase_anchoring, phase_locate  # noqa: E402
from core.card_draft import CardDraft, _valid_number_rows  # noqa: E402
from core.distiller import IDENTIFY_SYSTEM_PROMPT, Distiller  # noqa: E402
from core.request_context import LLM_CALLER, SYSTEM  # noqa: E402
from core.retro_cache import RETRO_VERSION  # noqa: E402
from core.retro_regions import ChapterRange, RetroRegions  # noqa: E402
from core.card_layers import REGISTRY  # noqa: E402
from storage.postgres_store import PostgresStore  # noqa: E402
from tests.test_longctx_focused_calls_goal import _USAGE, _group_llm  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "replay_distill.py"
_SPEC = _ROOT / "docs" / "specs" / "replay-distill.md"
_NAME = "祈灼"


def _text(chapter_of: dict[str, int], chapters: int = 350) -> str:
    """`chapter_of`：唯一锚点句 → 它所在的章号。每章都有标题与一行正文。

    前 25 章各多一句独有句子（S2），供重定位的判定替身选中，保证换上的锚点逐章唯一。
    """
    by_chapter = {n: sentence for sentence, n in chapter_of.items()}
    parts: list[str] = []
    for n in range(1, chapters + 1):
        parts.append(f"第{n}章\n这是本章的正文。\n")
        if n <= 25:
            # 行首不能是「第N章」：那样会被当成第二个章节标题（章号重复 → 全书无标题）。
            parts.append(f"独有句第{n}章。\n")
        if n in by_chapter:
            parts.append(by_chapter[n] + "\n")
    return "".join(parts)


_ARC = {
    "axis": "从孤冷到放手",
    "phases": [
        {"label": "孤冷求索", "state": "开头", "anchor": ""},
        {"label": "一见倾心", "state": "动心", "anchor": "他第一次看见她笑",
         "key_moments": ["他第一次看见她笑"]},
        {"label": "甘为底牌", "state": "守候", "anchor": "他把她护在身后",
         "key_moments": ["他把她护在身后"]},
        {"label": "甘愿放手", "state": "离开", "anchor": "他终于转身走了",
         "key_moments": ["他终于转身走了"]},
    ],
}
_ENTRY_QUOTES = {
    "行为": "他第一次看见她笑",
    "记忆": "他把她护在身后",
}


#: 阶段 2 / 4 可用的锚点 → 它们各自落在第几章。起点「落在真值外」只能靠换一句原文里
#: 真实存在的锚点，替身改章号本身对起点没有影响。真值：阶段 2 = 12–13，阶段 4 = 343–344；
#: 508 落在固定回忆区（491–504、508–509），399 落在章内回忆。
_PHASE2_ANCHORS = {
    12: "他第一次看见她笑",
    10: "他在第十章才看见她笑",
    13: "他在第十三章才看见她笑",
    14: "他在第十四章才看见她笑",
}
_PHASE4_ANCHORS = {
    344: "他终于转身走了",
    345: "他在第三百四十五章才转身走了",
    399: "他在第三百九十九章才转身走了",
    508: "他在第五百零八章才转身走了",
}


def _arc(chapter2: int = 12, *, chapter4: int = 344,
         phase2_anchor: str | None = None) -> dict:
    arc = {**_ARC, "phases": [{**p} for p in _ARC["phases"]]}
    arc["phases"][1]["anchor"] = phase2_anchor or _PHASE2_ANCHORS[chapter2]
    arc["phases"][3]["anchor"] = _PHASE4_ANCHORS[chapter4]
    return arc


def _chapter_map(chapter2: int = 12, chapter4: int = 344) -> dict[str, int]:
    return {
        _PHASE2_ANCHORS[chapter2]: chapter2,
        _PHASE2_ANCHORS[13]: 13,
        _PHASE2_ANCHORS[14]: 14,
        _PHASE2_ANCHORS[10]: 10,
        _PHASE4_ANCHORS[chapter4]: chapter4,
        _PHASE4_ANCHORS[345]: 345,
        "他把她护在身后": 122,
        "日记里的那句话": 205,
    }


def _payload(group: str, *, relationships: tuple[str, ...] = (),
             entries: dict | None = None) -> dict:
    base = CardDraft(name=_NAME).model_dump()
    if group == "G6":
        base["character_arc"] = _arc(12)
    if group == "G7":
        if entries is not None:
            if "situation_behaviors" in entries:
                base["situation_behaviors"] = entries["situation_behaviors"]
            if "key_memories" in entries:
                base["key_memories"] = entries["key_memories"]
        else:
            base["situation_behaviors"] = [{
                "situation": "见到她", "behavior": "沉默",
                "occurrences": [{"phase": 2, "quote": _ENTRY_QUOTES["行为"]}],
            }]
            base["key_memories"] = [{
                "memory": "护她",
                "occurrences": [{"phase": 3, "quote": _ENTRY_QUOTES["记忆"]}],
            }]
    if group == "G5":
        base["relationships"] = [{"target": t} for t in relationships]
    from core.schema import FORMAT_GROUPS
    return {k: base[k] for k in FORMAT_GROUPS[group]}


def _stub(*, arcs: list[int] | None = None, chapter4: int = 344,
          phase2_anchor: str | None = None, truncate_group: str | None = None,
          entries: dict | None = None):
    """组感知替身：弧线组按 `arcs` 轮换起点章号；可让某组在完整回包后报 `length` 截断。

    `invoked` / `callers` 是**替身自己**的调用计数与身份记录（复审 F7/F10）：T1 拿它
    对 recorder 的 `calls.jsonl` 行数，身份测试断言每条都读到 `SYSTEM`。
    截断分支照生产形态：上游以 `length` 终态结束（`IncompleteResponseError`），
    工具必须复用 `_truncation_evidence` 判，不许自己数 token。
    """
    llm, calls, _events = _group_llm()
    arcs = list(arcs or [12])
    state = {"i": 0}
    invoked: list[str] = []
    callers: list = []

    def mark(kind: str) -> None:
        invoked.append(kind)
        callers.append(LLM_CALLER.get(None))

    def chat(self, system, messages, max_tokens=None, **kw):
        mark("chat")
        if system.startswith(IDENTIFY_SYSTEM_PROMPT):
            return json.dumps([{"name": _NAME, "aliases": []}], ensure_ascii=False)
        if system == phase_locate.JUDGE_SYSTEM:
            user = messages[0]["content"]
            chapter = int(re.search(r"### 第(\d+)章", user).group(1))
            sent = next((line.split("：")[0] for line in user.splitlines()
                         if line.startswith("S") and f"独有句第{chapter}章。" in line), "")
            return json.dumps({"happens": bool(sent), "sent": sent, "why": "stub"},
                              ensure_ascii=False)
        return json.dumps(CardDraft(name=_NAME).model_dump(), ensure_ascii=False)

    async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
        mark("async_chat")
        return ("片段分析", _USAGE)

    def chat_stream_long(self, system, messages, max_tokens=None, **kw):
        mark("chat_stream_long")
        if "标出倒叙的章节" in system:
            yield '{"ranges": []}'
            return _USAGE
        match = re.search(r"CharacterCard\[(G\d)\]", system)
        if match is None:
            yield json.dumps(_payload("G1") if "CharacterCard[G1]" in system
                             else CardDraft(name=_NAME).model_dump(), ensure_ascii=False)
            return _USAGE
        group = match.group(1)
        if group == "G6":
            chapter = arcs[min(state["i"], len(arcs) - 1)]
            state["i"] += 1
            calls.append({"group": group, "system": system, "messages": messages,
                          "max_tokens": max_tokens})
            yield json.dumps({"character_arc": _arc(
                chapter, chapter4=chapter4, phase2_anchor=phase2_anchor)},
                ensure_ascii=False)
            return _USAGE
        calls.append({"group": group, "system": system, "messages": messages,
                      "max_tokens": max_tokens})
        if group == "G7":
            payload = _payload("G7", entries=entries)
        elif group == "G5":
            payload = {"relationships": []}
        else:
            payload = _payload(group)
        text = json.dumps(payload, ensure_ascii=False)
        yield text
        if group == truncate_group:
            raise IncompleteResponseError("length", "chat_stream", "")
        return _USAGE

    llm.base_url = "https://stub.invalid"
    llm.chat = types.MethodType(chat, llm)
    llm.async_chat = types.MethodType(async_chat, llm)
    llm.chat_stream_long = types.MethodType(chat_stream_long, llm)
    llm._stub_invoked = invoked
    llm._stub_callers = callers
    return llm


async def _prepare(chapter2: int = 12, *, chapter4: int = 344, chapters: int = 350,
                   retro: RetroRegions | None = None, seed: bool = True):
    """`seed=False` 时不写名单 / 回忆区缓存，让这两步真的调模型（复审 F3）。"""
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    tag = uuid.uuid4().hex[:10]
    user = await store.create_user(f"u_{tag}", f"n_{tag}", "x")
    text = await store.save_text(
        f"t_{tag}", "f.txt", _text(_chapter_map(chapter2, chapter4), chapters),
        user_id=user["id"])
    if seed:
        await store.save_characters(
            text["id"], [{"name": _NAME, "aliases": []}], version=Distiller.IDENTIFY_VERSION)
        await store.save_retro_regions(
            text["id"], (retro or RetroRegions("marked")).to_json(), version=RETRO_VERSION)
    return store, user, text


async def _run(store, text_id: str, out_dir: Path, llm, *, runs: int = 1, truth: str = _NAME):
    import scripts.replay_distill as replay
    from web import deps as web_deps

    # 工具现在走生产出口；替身从这里注入，与 `test_replay_owner_llm_goal.py` 同一口径。
    original = web_deps.get_user_llm

    async def fake_user_llm(user_id, storage=None):
        return llm

    web_deps.get_user_llm = fake_user_llm
    try:
        return await replay.run_replay(
            store, text_id=text_id, name=_NAME, runs=runs, out_dir=out_dir,
            truth_name=truth)
    finally:
        web_deps.get_user_llm = original


def test_t1_one_run_writes_six_files_and_no_writes(monkeypatch, tmp_path):
    async def go():
        store, user, text = await _prepare()
        llm = _stub()
        try:
            summary = await _run(store, text["id"], tmp_path, llm)
            return store, user, text, summary, llm
        finally:
            await store.close()

    store, user, text, summary, llm = asyncio.run(go())
    run_dir = tmp_path / "run-1"
    names = {p.name for p in run_dir.iterdir()}
    assert names == {
        "calls.jsonl", "draft_before_locate.json", "draft_after_locate.json",
        "retro.json", "phase_starts.json", "entries.tsv"}, names
    lines = [json.loads(x) for x in (run_dir / "calls.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    # 期望次数由替身自己数（复审 F7）：不再拿 recorder 的计数对它的产物。
    assert len(lines) == len(llm._stub_invoked) > 0
    assert summary["runs"][0]["calls"] == len(llm._stub_invoked)

    async def check():
        s = PostgresStore(TEST_DATABASE_URL)
        await s._ensure_initialized()
        try:
            async with await s._connect() as conn:
                tasks = await conn.fetchval(
                    "SELECT count(*) FROM distill_tasks WHERE user_id = $1", user["id"])
                cards = await conn.fetchval(
                    "SELECT count(*) FROM cards WHERE user_id = $1", user["id"])
        finally:
            await s.close()
        return tasks, cards

    assert asyncio.run(check()) == (0, 0)


def test_t2_entries_landed_phase_matches_phase_anchoring(monkeypatch, tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            await _run(store, text["id"], tmp_path, _stub())
            content = (await store.get_text_unscoped(text["id"]))["content"]
        finally:
            await store.close()
        return content

    content = asyncio.run(go())
    run_dir = tmp_path / "run-1"
    draft = json.loads((run_dir / "draft_after_locate.json").read_text(encoding="utf-8"))
    model = CardDraft.model_validate(draft)
    anchors = phase_anchoring.build_anchors(
        model.character_arc.phases, content,
        retro=RetroRegions("marked").spans(content))
    count = len(model.character_arc.phases)
    rows = [line.split("\t") for line in (run_dir / "entries.tsv").read_text(
        encoding="utf-8").splitlines()[1:]]

    def expected(items, kind: str, layer: str):
        valid = _valid_number_rows(items, count, kind, layer=layer).valid
        return phase_anchoring.verify(items, valid, anchors, kind=kind)

    b = expected(model.situation_behaviors, "做法",
                 REGISTRY["situation_behaviors"].layer)
    m = expected(model.key_memories, "记忆",
                 REGISTRY["key_memories"].layer)
    for i, row in enumerate([r for r in rows if r[0] == "situation_behaviors"]):
        assert row[3] == ",".join(
            str(p) for p in sorted({t for ts in b.tag_targets[i].values() for t in ts}))
    for i, row in enumerate([r for r in rows if r[0] == "key_memories"]):
        assert row[3] == ",".join(
            str(p) for p in sorted({t for ts in m.tag_targets[i].values() for t in ts}))


def test_t3_phase2_inside_truth_passes(monkeypatch, tmp_path):
    async def go():
        store, user, text = await _prepare(chapter2=12)
        try:
            return await _run(store, text["id"], tmp_path, _stub(arcs=[12, 12]), runs=2)
        finally:
            await store.close()

    summary = asyncio.run(go())
    assert summary["H2"]["verdict"] == "过", json.dumps(summary["H2"], ensure_ascii=False)
    assert [r["verdict"] for r in summary["H2"]["runs"]] == ["过", "过"]


def test_t3_phase2_outside_truth_fails_overall(monkeypatch, tmp_path):
    async def go():
        store, user, text = await _prepare(chapter2=12)
        try:
            # 第一次起点在真值内（12），第二次在真值外（10）—— 两次都过才算过
            return await _run(store, text["id"], tmp_path, _stub(arcs=[12, 10]), runs=2)
        finally:
            await store.close()

    summary = asyncio.run(go())
    assert [r["verdict"] for r in summary["H2"]["runs"]] == ["过", "不过"]
    assert summary["H2"]["verdict"] == "不过"


def test_t4_length_terminal_state_marks_h5_suspected_truncation(monkeypatch, tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            return await _run(
                store, text["id"], tmp_path, _stub(truncate_group="G1"))
        finally:
            await store.close()

    summary = asyncio.run(go())
    assert summary["H5"]["verdict"] == "不过"
    assert "疑似截断" in summary["H5"]["note"]
    calls = [json.loads(x) for x in (tmp_path / "run-1" / "calls.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    truncated = [c for c in calls if c["truncated"]]
    assert truncated, calls
    # F6：截断拿不到 usage —— 写 null，不许当 0 计入成本；汇总注明未知次数。
    assert all(c["usage"] is None for c in truncated), truncated
    assert summary["usage"]["unknown_usage_calls"] == len(truncated)
    assert f"{len(truncated)} 次调用用量未知" in summary["usage"]["note"]


def test_t5_no_write_path_names_and_db_untouched(monkeypatch, tmp_path):
    src = _SCRIPT.read_text(encoding="utf-8")
    for name in ("save_card", "finalize_card", "_auto_tag",
                 "create_distill_task", "save_distill_chunk", "_persist_chunk"):
        assert name not in src, f"工具源码出现写路径名：{name}"

    async def go():
        store, user, text = await _prepare()
        try:
            await _run(store, text["id"], tmp_path, _stub())
            async with await store._connect() as conn:
                tasks = await conn.fetchval(
                    "SELECT count(*) FROM distill_tasks WHERE user_id = $1", user["id"])
                cards = await conn.fetchval(
                    "SELECT count(*) FROM cards WHERE user_id = $1", user["id"])
                usage = await conn.fetchval(
                    "SELECT count(*) FROM usage_stats WHERE user_id = $1", user["id"])
        finally:
            await store.close()
        return tasks, cards, usage

    assert asyncio.run(go()) == (0, 0, 0)


# ── T6：判定线逐字 / 逐值照抄 ────────────────────────────────────────────

def _spec_checklist() -> list[str]:
    text = _SPEC.read_text(encoding="utf-8")
    block = re.search(
        r"清单（逐字照抄跟踪文档原文）：\n(.*?)\n   - 真值：", text, re.S)
    assert block, "spec 里找不到验收清单块"
    return [line.strip()[3:].strip() for line in block.group(1).splitlines()
            if re.match(r"\s+[123]\. ", line)]


def _spec_truth() -> dict[str, dict[int, tuple[str, int, int]]]:
    text = _SPEC.read_text(encoding="utf-8")
    line = re.search(r"真值：(.*)", text).group(1)
    out: dict[str, dict[int, tuple[str, int, int]]] = {}
    for chunk in re.split(r"(?=(?:祈灼|谢凛羽) )", line):
        if not chunk.strip():
            continue
        name = chunk.split()[0]
        out[name] = {
            int(k): (label, int(lo), int(hi or lo))
            for k, label, lo, hi in re.findall(
                r"(\d+) (\S+) (\d+)(?:–(\d+))?", chunk)
        }
    return out


def test_t6_checklist_and_truth_are_verbatim():
    import scripts.replay_distill as replay
    assert list(replay.ACCEPTANCE_CHECKLIST) == _spec_checklist()
    assert replay.TRUTH == _spec_truth()


# ── T7：逐条判定与 core 同口径（复审 F4/F9）──────────────────────────────

#: 三条样本，分别对应「一致 / 改挂 / 未定位」：
#: - 一致：标 2、3 两个阶段；阶段 2 的摘录查不到（该阶段没有位置证据），阶段 3 的摘录落在 3。
#:   旧工具把「某阶段无位置证据」误判成改挂（漏 `ts and`）。
#: - 改挂：标阶段 2，摘录实际落在阶段 3。
#: - 未定位：只有阶段 2，摘录查不到。
_VERDICT_ENTRIES = {
    "situation_behaviors": [
        {"situation": "一致条目", "behavior": "沉默", "occurrences": [
            {"phase": 2, "quote": "阶段二查无此句"},
            {"phase": 3, "quote": _ENTRY_QUOTES["记忆"]},
        ]},
        {"situation": "改挂条目", "behavior": "沉默", "occurrences": [
            {"phase": 2, "quote": _ENTRY_QUOTES["记忆"]},
        ]},
        {"situation": "未定位条目", "behavior": "沉默", "occurrences": [
            {"phase": 2, "quote": "另一句查无此句"},
        ]},
    ],
    "key_memories": [
        {"memory": "一致记忆", "occurrences": [
            {"phase": 2, "quote": "阶段二另查无此句"},
            {"phase": 3, "quote": _ENTRY_QUOTES["记忆"]},
        ]},
        {"memory": "改挂记忆", "occurrences": [
            {"phase": 2, "quote": _ENTRY_QUOTES["记忆"]},
        ]},
        {"memory": "未定位记忆", "occurrences": [
            {"phase": 2, "quote": "再一句查无此句"},
        ]},
    ],
}


def test_t7_entry_verdicts_match_core_verify_counts(tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            await _run(store, text["id"], tmp_path, _stub(entries=_VERDICT_ENTRIES))
            content = (await store.get_text_unscoped(text["id"]))["content"]
        finally:
            await store.close()
        return content

    content = asyncio.run(go())
    run_dir = tmp_path / "run-1"
    draft = json.loads((run_dir / "draft_after_locate.json").read_text(encoding="utf-8"))
    model = CardDraft.model_validate(draft)
    anchors = phase_anchoring.build_anchors(
        model.character_arc.phases, content,
        retro=RetroRegions("marked").spans(content))
    count = len(model.character_arc.phases)

    verdicts: dict[str, list[str]] = {}
    for line in (run_dir / "entries.tsv").read_text(
            encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        verdicts.setdefault(cols[0], []).append(cols[4])

    for name, items, kind in (
            ("situation_behaviors", model.situation_behaviors, "做法"),
            ("key_memories", model.key_memories, "记忆")):
        valid = _valid_number_rows(items, count, kind, layer=REGISTRY[name].layer).valid
        result = phase_anchoring.verify(items, valid, anchors, kind=kind)
        got = verdicts[name]
        assert got == ["一致", "改挂", "未定位"], (name, got)
        assert got.count("改挂") == result.rehung, (name, got, result.rehung)
        assert got.count("未定位") == len(result.unlocated), (name, got, result.unlocated)


def _entry_card() -> CardDraft:
    """一份每类被核对的条目各一条的草稿：两类专有 + 状态/经历类 + 关系态度。"""
    data = CardDraft(name=_NAME).model_dump()
    data["character_arc"] = _arc(12)
    data["situation_behaviors"] = [{
        "situation": "见到她", "behavior": "沉默",
        "occurrences": [{"phase": 2, "quote": _ENTRY_QUOTES["行为"]}],
    }]
    data["key_memories"] = [{
        "memory": "护她",
        "occurrences": [{"phase": 3, "quote": _ENTRY_QUOTES["记忆"]}],
    }]
    data["values"] = [{
        "value": "守护",
        "occurrences": [{"phase": 3, "quote": _ENTRY_QUOTES["记忆"]}],
    }]
    data["cognitive"]["knowledge_scope"] = [{
        "value": "只知道宗门",
        "occurrences": [{"phase": 2, "quote": _ENTRY_QUOTES["行为"]}],
    }]
    data["relationships"] = [{
        "target": "谢凛羽",
        "attitudes": [{"phase": 2, "quote": _ENTRY_QUOTES["行为"],
                       "attitude": "想靠近", "note": ""}],
    }]
    return CardDraft.model_validate(data)


def test_t8_entries_cover_every_category_core_verifies():
    """N2：`card_from_draft` 调 `verify` 的每一类都要进 entries.tsv / H1。"""
    import scripts.replay_distill as replay
    content = _text(_chapter_map())
    model = _entry_card()
    anchors = phase_anchoring.build_anchors(model.character_arc.phases, content)

    rows = replay._entries(model, anchors, content)

    fields = [row["field"] for row in rows]
    assert fields == ["situation_behaviors", "key_memories", "relationships.attitudes",
                      "values", "cognitive.knowledge_scope"], fields
    assert [row["verdict"] for row in rows] == ["一致"] * len(fields), rows
    h1 = replay._h1(rows, len(model.character_arc.phases))
    assert h1["consistent"] == len(fields), h1
    assert h1["no_valid_tag"] == 0, h1


def test_t9_rows_without_legal_phase_are_not_counted_consistent():
    """N3：只标 0 / 编号非法的条目不进「一致」，单列「无合法标注」。"""
    import scripts.replay_distill as replay
    content = _text(_chapter_map())
    data = CardDraft(name=_NAME).model_dump()
    data["character_arc"] = _arc(12)
    data["values"] = [
        {"value": "全程", "occurrences": [{"phase": 0, "quote": _ENTRY_QUOTES["记忆"]}]},
        {"value": "写坏编号", "occurrences": [{"phase": 99, "quote": _ENTRY_QUOTES["行为"]}]},
        {"value": "阶段三", "occurrences": [{"phase": 3, "quote": _ENTRY_QUOTES["记忆"]}]},
    ]
    model = CardDraft.model_validate(data)
    anchors = phase_anchoring.build_anchors(model.character_arc.phases, content)

    rows = replay._entries(model, anchors, content)

    assert [row["verdict"] for row in rows] == ["无合法标注", "无合法标注", "一致"], rows
    h1 = replay._h1(rows, len(model.character_arc.phases))
    assert h1["no_valid_tag"] == 2, h1
    assert h1["consistent"] == 1, h1


def test_checklist_item1_fails_when_position_check_is_skipped():
    """N5：锚点落进回忆区 → 整卡跳过 → 清单第 1 条必须判「不过」。"""
    import scripts.replay_distill as replay
    content = _text(_chapter_map())
    phases = _arc(12)["phases"]
    phases[1]["anchor"] = "日记里的那句话"          # 第 205 章
    retro = RetroRegions("marked", (ChapterRange(200, 210, "日记"),))
    anchors = phase_anchoring.build_anchors(phases, content, retro=retro.spans(content))
    assert anchors.reason, anchors.reason

    starts = replay._start_rows(phases, anchors, content,
                                [p.get("anchor", "") for p in phases])
    items = replay._checklist(starts, phases, anchors, _NAME)

    assert items[0]["verdict"] == "不过", items[0]
    assert items[0]["detail"] == anchors.reason, items[0]


# ── 复审补充：H4 触发 / 不触发（M2、M13）────────────────────────────────────

def _run_calls(run_dir: Path) -> list[dict]:
    return [json.loads(x) for x in (run_dir / "calls.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]


def test_h4_not_triggered_when_anchors_pass(tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            return await _run(store, text["id"], tmp_path, _stub())
        finally:
            await store.close()

    summary = asyncio.run(go())
    h4 = summary["H4"]["runs"][0]
    starts = json.loads((tmp_path / "run-1" / "phase_starts.json").read_text(
        encoding="utf-8"))
    assert h4["triggered"] is False, h4
    assert starts["relocated"] is False, starts
    assert h4["before"] == h4["after"], h4
    assert not [c for c in _run_calls(tmp_path / "run-1")
                if c["action"] == "distill_phase_locate"]


def test_h4_triggered_when_anchor_unlocatable(tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            return await _run(store, text["id"], tmp_path,
                              _stub(phase2_anchor="原文里没有这句话"))
        finally:
            await store.close()

    summary = asyncio.run(go())
    h4 = summary["H4"]["runs"][0]
    starts = json.loads((tmp_path / "run-1" / "phase_starts.json").read_text(
        encoding="utf-8"))
    assert h4["triggered"] is True, h4
    assert starts["relocated"] is True, starts
    assert h4["before"][1] == "原文里没有这句话", h4
    assert h4["after"][1] == "独有句第1章。", h4
    assert [p["chapter"] for p in starts["phases"]] == [0, 1, 2, 3], starts
    assert [c for c in _run_calls(tmp_path / "run-1")
            if c["action"] == "distill_phase_locate"]


# ── 复审补充：回忆区（M3）───────────────────────────────────────────────────

def test_retro_regions_reach_card_from_draft(monkeypatch, tmp_path):
    import scripts.replay_distill as replay
    seen: dict = {}
    real = replay.card_from_draft

    def spy(draft, content, *, retro):
        seen["retro"] = retro
        return real(draft, content, retro=retro)

    monkeypatch.setattr(replay, "card_from_draft", spy)
    retro = RetroRegions("marked", (ChapterRange(200, 210, "日记"),))
    entries = {"situation_behaviors": [{
        "situation": "回忆区条目", "behavior": "沉默",
        "occurrences": [{"phase": 2, "quote": "日记里的那句话"}],
    }]}

    async def go():
        store, user, text = await _prepare(retro=retro)
        try:
            await _run(store, text["id"], tmp_path, _stub(entries=entries))
        finally:
            await store.close()

    asyncio.run(go())
    rows = [line.split("\t") for line in (tmp_path / "run-1" / "entries.tsv").read_text(
        encoding="utf-8").splitlines()[1:]]
    row = next(r for r in rows if r[1] == "回忆区条目")
    # 摘录只在回忆区（第 205 章）里出现：不作位置证据 → 保留模型标注，不改挂。
    assert row[4] == "一致", row
    assert seen["retro"].ranges == retro.ranges, seen


# ── 复审补充：真值边界（M4b）───────────────────────────────────────────────

def test_truth_boundary_phase2_13_passes_14_fails(tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            return await _run(store, text["id"], tmp_path, _stub(arcs=[13, 14]), runs=2)
        finally:
            await store.close()

    summary = asyncio.run(go())
    got = [r["items"][2] for r in summary["H2"]["runs"]]
    assert [i["verdict"] for i in got] == ["过", "不过"], got


def test_truth_boundary_phase4_344_passes_345_fails(tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            inside = await _run(store, text["id"], tmp_path / "a",
                                _stub(arcs=[12], chapter4=344))
            outside = await _run(store, text["id"], tmp_path / "b",
                                 _stub(arcs=[12], chapter4=345))
        finally:
            await store.close()
        return inside, outside

    inside, outside = asyncio.run(go())
    assert inside["H2"]["runs"][0]["items"][2]["verdict"] == "过"
    assert outside["H2"]["runs"][0]["items"][2]["verdict"] == "不过"


# ── 真值按起点章匹配（10-22：阶段名每次蒸馏都不同，不再按名字对）──────────────
#
# 10-22 小重放 4 次里 3 次判「看不出」：`_checklist` 只在阶段号相同且 label 逐字等于
# 真值表时才比。跟踪文档的验收口径是「阶段对准真实转折」，所以按**起点落在哪一章**判。

def _synthetic_starts(*chapters: int | None) -> list[dict]:
    return [{"phase": n, "chapter": c} for n, c in enumerate(chapters, start=1)]


class _NoSkip:
    reason = ""


def _item3(chapters, *, truth: str | None = _NAME) -> dict:
    """直接喂起点章号：判定只该看章，不该看阶段名 / 阶段数。"""
    import scripts.replay_distill as replay
    phases = [{"label": f"阶段{n}"} for n in range(1, len(chapters) + 1)]
    return replay._checklist(_synthetic_starts(*chapters), phases, _NoSkip(), truth)[2]


@pytest.mark.xfail(strict=True, reason="目标：阶段名不同、起点都在真值内 → 过")
def test_truth_matches_by_start_chapter_not_phase_name():
    item = _item3([0, 13, 122, 343])
    assert item["verdict"] == "过", item
    assert "一见倾心" in item["detail"], item


@pytest.mark.xfail(strict=True, reason="目标：缺真值转折判不过并点名")
def test_missing_truth_transition_fails_and_names_it():
    # 祈灼 run2 的形态：3 个阶段，起点 0 / 122 / 343，12–13 这一转没对上
    item = _item3([0, 122, 343])
    assert item["verdict"] == "不过", item
    assert "真值「一见倾心」12–13" in item["detail"], item


@pytest.mark.xfail(strict=True, reason="目标：多出的起点只报告，不因此判不过")
def test_extra_start_outside_truth_is_reported_but_passes():
    item = _item3([0, 13, 122, 343, 424])
    assert item["verdict"] == "过", item
    assert "424" in item["detail"], item


@pytest.mark.xfail(strict=True, reason="目标：书名开头不算多出的起点")
def test_book_start_is_not_reported_as_an_extra_start():
    # 谢凛羽 run2 的形态：0 / 29 / 424；唯一多出的起点是 424，不是全书开头
    item = _item3([0, 29, 424], truth="谢凛羽")
    assert item["verdict"] == "不过", item
    assert "424" in item["detail"], item
    assert "多出的起点：0" not in item["detail"], item


# ── 复审补充：固定回忆章（M6、M7）──────────────────────────────────────────

def test_fixed_retro_chapter_508_fails_checklist_item2(tmp_path):
    async def go():
        store, user, text = await _prepare(chapter4=508, chapters=520)
        try:
            return await _run(store, text["id"], tmp_path,
                              _stub(arcs=[12], chapter4=508))
        finally:
            await store.close()

    item2 = asyncio.run(go())["H2"]["runs"][0]["items"][1]
    assert item2["verdict"] == "不过", item2
    assert "第508章" in item2["detail"], item2


def test_in_chapter_recall_chapter_399_fails_checklist_item2(tmp_path):
    async def go():
        store, user, text = await _prepare(chapter4=399, chapters=520)
        try:
            return await _run(store, text["id"], tmp_path,
                              _stub(arcs=[12], chapter4=399))
        finally:
            await store.close()

    item2 = asyncio.run(go())["H2"]["runs"][0]["items"][1]
    assert item2["verdict"] == "不过", item2
    assert "第399章" in item2["detail"], item2


# ── 复审补充：H5 通过路（M12）与身份（M10）────────────────────────────────

def test_h5_passes_without_truncation(tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            return await _run(store, text["id"], tmp_path, _stub())
        finally:
            await store.close()

    h5 = asyncio.run(go())["H5"]
    assert h5["verdict"] == "过", h5
    assert h5["truncated_calls"] == [], h5


def test_every_llm_call_sees_system_caller(tmp_path):
    async def go():
        store, user, text = await _prepare()
        llm = _stub()
        try:
            await _run(store, text["id"], tmp_path, llm)
        finally:
            await store.close()
        return llm

    llm = asyncio.run(go())
    assert llm._stub_callers, "替身一次都没被调用"
    assert all(c is SYSTEM for c in llm._stub_callers), llm._stub_callers


# ── 复审补充：名单 / 回忆区调用记全（F3）──────────────────────────────────

def test_setup_calls_land_in_calls_jsonl(tmp_path):
    async def go():
        store, user, text = await _prepare(chapters=2, seed=False)
        try:
            return await _run(store, text["id"], tmp_path, _stub())
        finally:
            await store.close()

    summary = asyncio.run(go())
    path = tmp_path / "calls.jsonl"
    rows = [json.loads(x) for x in path.read_text(
        encoding="utf-8").splitlines() if x.strip()]
    assert rows and all(r["stage"] == "setup" for r in rows), rows
    actions = {r["action"] for r in rows}
    # 名单走 adapter.chat；回忆区走 _collect_stream（直连 adapter，无 annotate 的 label）。
    assert actions == {"chat", "chat_stream_long"}, actions
    assert summary["setup_calls"] == len(rows)
    assert summary["usage"]["calls"] == len(rows) + summary["runs"][0]["calls"]


def test_summary_records_model_and_base_url(tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            return await _run(store, text["id"], tmp_path, _stub())
        finally:
            await store.close()

    summary = asyncio.run(go())
    assert summary["model"] == "stub"
    assert summary["base_url"] == "https://stub.invalid"
