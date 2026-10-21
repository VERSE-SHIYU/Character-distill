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
from core import phase_anchoring  # noqa: E402
from core.card_draft import CardDraft, _GENERIC_PATHS, _valid_number_rows  # noqa: E402
from core.distiller import Distiller  # noqa: E402
from core.retro_cache import RETRO_VERSION  # noqa: E402
from core.retro_regions import RetroRegions  # noqa: E402
from core.card_layers import REGISTRY  # noqa: E402
from storage.postgres_store import PostgresStore  # noqa: E402
from tests.test_longctx_focused_calls_goal import _USAGE, _group_llm  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "replay_distill.py"
_SPEC = _ROOT / "docs" / "specs" / "replay-distill.md"
_NAME = "祈灼"


def _text(chapter_of: dict[str, int], chapters: int = 350) -> str:
    """`chapter_of`：唯一锚点句 → 它所在的章号。每章都有标题与一行正文。"""
    by_chapter = {n: sentence for sentence, n in chapter_of.items()}
    parts: list[str] = []
    for n in range(1, chapters + 1):
        parts.append(f"第{n}章\n这是本章的正文。\n")
        if n in by_chapter:
            parts.append(by_chapter[n] + "\n")
    return "".join(parts)


_ARC = {
    "axis": "从孤冷到放手",
    "phases": [
        {"label": "孤冷求索", "state": "开头", "anchor": ""},
        {"label": "一见倾心", "state": "动心", "anchor": "他第一次看见她笑"},
        {"label": "甘为底牌", "state": "守候", "anchor": "他把她护在身后"},
        {"label": "甘愿放手", "state": "离开", "anchor": "他终于转身走了"},
    ],
}
_ENTRY_QUOTES = {
    "行为": "他第一次看见她笑",
    "记忆": "他把她护在身后",
}


#: 阶段 2 可用的两种锚点 → 它们各自落在第几章。起点「落在真值外」只能靠换一句原文里
#: 真实存在的锚点，替身改章号本身对起点没有影响。
_PHASE2_ANCHORS = {12: "他第一次看见她笑", 10: "他在第十章才看见她笑"}


def _arc(chapter2: int) -> dict:
    arc = {**_ARC, "phases": [{**p} for p in _ARC["phases"]]}
    arc["phases"][1]["anchor"] = _PHASE2_ANCHORS[chapter2]
    return arc


def _chapter_map(chapter2: int) -> dict[str, int]:
    return {
        _PHASE2_ANCHORS[chapter2]: chapter2,
        _PHASE2_ANCHORS[10]: 10,
        "他把她护在身后": 122,
        "他终于转身走了": 344,
    }


def _payload(group: str, *, relationships: tuple[str, ...] = ()) -> dict:
    base = CardDraft(name=_NAME).model_dump()
    if group == "G6":
        base["character_arc"] = _arc(12)
    if group == "G7":
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


def _stub(*, arcs: list[int] | None = None, truncate_group: str | None = None):
    """组感知替身：弧线组按 `arcs` 轮换起点章号；可让某组在完整回包后报 `length` 截断。

    截断分支照生产形态：上游以 `length` 终态结束（`IncompleteResponseError`），
    工具必须复用 `_truncation_evidence` 判，不许自己数 token。
    """
    llm, calls, _events = _group_llm()
    arcs = list(arcs or [12])
    state = {"i": 0}

    def chat_stream_long(self, system, messages, max_tokens=None, **kw):
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
            yield json.dumps({"character_arc": _arc(chapter)}, ensure_ascii=False)
            return _USAGE
        calls.append({"group": group, "system": system, "messages": messages,
                      "max_tokens": max_tokens})
        if group == "G7":
            payload = _payload("G7")
        elif group == "G5":
            payload = {"relationships": []}
        else:
            payload = _payload(group)
        text = json.dumps(payload, ensure_ascii=False)
        yield text
        if group == truncate_group:
            raise IncompleteResponseError("length", "chat_stream", "")
        return _USAGE

    llm.chat_stream_long = types.MethodType(chat_stream_long, llm)
    return llm


async def _prepare(chapter2: int = 12):
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    tag = uuid.uuid4().hex[:10]
    user = await store.create_user(f"u_{tag}", f"n_{tag}", "x")
    text = await store.save_text(
        f"t_{tag}", "f.txt", _text(_chapter_map(chapter2)), user_id=user["id"])
    await store.save_characters(
        text["id"], [{"name": _NAME, "aliases": []}], version=Distiller.IDENTIFY_VERSION)
    await store.save_retro_regions(
        text["id"], RetroRegions("marked").to_json(), version=RETRO_VERSION)
    return store, user, text


async def _run(store, text_id: str, out_dir: Path, llm, *, runs: int = 1, truth: str = _NAME):
    import scripts.replay_distill as replay
    return await replay.run_replay(
        store, llm, text_id=text_id, name=_NAME, runs=runs, out_dir=out_dir,
        truth_name=truth)


def test_t1_one_run_writes_six_files_and_no_writes(monkeypatch, tmp_path):
    async def go():
        store, user, text = await _prepare()
        try:
            summary = await _run(store, text["id"], tmp_path, _stub())
            return store, user, text, summary
        finally:
            await store.close()

    store, user, text, summary = asyncio.run(go())
    run_dir = tmp_path / "run-1"
    names = {p.name for p in run_dir.iterdir()}
    assert names == {
        "calls.jsonl", "draft_before_locate.json", "draft_after_locate.json",
        "retro.json", "phase_starts.json", "entries.tsv"}, names
    lines = [json.loads(x) for x in (run_dir / "calls.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == summary["runs"][0]["calls"] > 0

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
    assert any(c["truncated"] for c in calls), calls


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
