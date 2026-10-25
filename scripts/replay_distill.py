# -*- coding: utf-8 -*-
"""蒸馏小重放：走生产同一条链到 `card_from_draft` 为止，落盘每次调用的原文与判据。

    python scripts/replay_distill.py --text-id <id> --name 祈灼 --runs 2 \\
        --out /tmp/replay-<时间戳> [--truth 祈灼]

**不写库**：不存卡、不写 `distill_tasks`、不打标签、不做后置收尾。整段重放包在
`system_llm_context()` 里 —— 记账出口据此读不到用户身份，用量只进 `calls.jsonl` /
`summary.json`，不进 `usage_stats`（`web/llm_gate.py:65-67` 要求调用方身份，不能靠「不设
LLM_CALLER」；`core/request_context.py:54` 的 `system_llm_context` 是请求外的唯一声明口）。

**模型取属主自己的配置**：引导读拿到文本属主后走 `web/deps.py:136` `get_user_llm(user_id)`
—— 与生产同一条出口，不回落全局（`get_llm()`）。属主没配 key 时直接退出。

名单缓存 / 回忆区缓存与生产同口径写回（它们是书的属性，第二次重放才有得命中）。

判定线照抄跟踪文档（Tracy 10-19 12:09 定，跑前写死、事后不改）；`tests/test_replay_distill_goal.py`
的 T6 逐字锁住这里的常量。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.card_draft import (  # noqa: E402
    CardDraft, DraftMemory, DraftOccurrence, _GENERIC_PATHS, _valid_number_rows,
    card_from_draft)
from core.card_layers import REGISTRY, get_path  # noqa: E402
from core.character_roster import aliases_for, resolve_characters  # noqa: E402
from core.distiller import Distiller  # noqa: E402
from core.llm_json import loads_llm_json  # noqa: E402
from core.phase_anchoring import Placement, build_anchors, chapter_labels, verify  # noqa: E402
from core.quotes import locate_in_normalized, raw_offsets  # noqa: E402
from core.request_context import system_llm_context  # noqa: E402
from core.retro_cache import resolve_retro_regions  # noqa: E402
from core.utils import CACHE_HIT_FIELD, CACHE_MISS_FIELD  # noqa: E402

# ── 判定线（逐字照抄跟踪文档「祈灼卡修复 · 当前问题」验收清单与真值表）──
ACCEPTANCE_CHECKLIST = [
    "卡能选阶段：所有起点齐全、严格递增，日志里没有「整卡跳过」。",
    "每个阶段起点都不在回忆区（第 491–504、508–509 章），也不在第 383、399 章的章内回忆。",
    "与真值表对得上的阶段，起点落在真值范围内。",
]
TRUTH = {
    "祈灼": {
        1: ("孤冷求索", 0, 0),
        2: ("一见倾心", 12, 13),
        3: ("甘为底牌", 122, 122),
        4: ("甘愿放手", 343, 344),
    },
    "谢凛羽": {
        2: ("心动沦陷", 29, 30),
        3: ("甘愿共享", 460, 461),
    },
}
RETRO_CHAPTER_RANGES = ((491, 504), (508, 509))
IN_CHAPTER_RECALL = frozenset({383, 399})

_GROUP_RE = re.compile(r"CharacterCard\[(G\d)\]")
_CHAPTER_RE = re.compile(r"第(\d+)章")


# ── 记录型适配器：只旁听，不改生产代码 ─────────────────────────────────

class _Recorder:
    """一次运行内的调用记录。组在并发线程里跑，追加与计数都要上锁。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._local = threading.local()

    def annotate(self, label: str, action: str) -> None:
        self._local.label = label
        self._local.action = action

    def clear(self) -> None:
        self._local.label = ""
        self._local.action = ""

    def note(self, *, method: str, system_prompt: str, usage: dict | None,
             output: str, seconds: float, truncated: bool, error: str = "") -> None:
        group = _GROUP_RE.search(system_prompt)
        label = getattr(self._local, "label", "") or ""
        action = getattr(self._local, "action", "") or (
            "format:" + group.group(1) if group else method)
        with self._lock:
            self.calls.append({
                "n": len(self.calls) + 1,
                "group": group.group(1) if group else "",
                "action": action,
                "label": label,
                "output": output,
                # 拿不到用量写 null（截断 / 调用失败）：null 与「0 token」必须可分辨。
                "usage": usage or None,
                "duration_s": round(seconds, 3),
                "truncated": truncated,
                "error": error,
            })


class _RecordingLLM:
    """透传适配器调用，顺手把原文与用量记下来。"""

    def __init__(self, inner: Any, recorder: _Recorder) -> None:
        self._inner = inner
        self._rec = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def chat(self, system_prompt: str, messages: list[dict], max_tokens: int | None = None,
             **kw: Any) -> str:
        started = time.monotonic()
        try:
            reply = self._inner.chat(system_prompt, messages, max_tokens=max_tokens, **kw)
        except Exception as exc:
            self._rec.note(
                method="chat", system_prompt=system_prompt, usage=None, output="",
                seconds=time.monotonic() - started, error=type(exc).__name__,
                truncated=bool(Distiller._truncation_evidence(exc)))
            raise
        self._rec.note(
            method="chat", system_prompt=system_prompt, output=reply,
            usage=getattr(self._inner, "last_usage", None),
            seconds=time.monotonic() - started, truncated=False)
        return reply

    async def async_chat(self, system_prompt: str, messages: list[dict],
                         max_tokens: int | None = None, **kw: Any) -> tuple[str, dict | None]:
        started = time.monotonic()
        try:
            reply, usage = await self._inner.async_chat(
                system_prompt, messages, max_tokens=max_tokens, **kw)
        except Exception as exc:
            self._rec.note(
                method="async_chat", system_prompt=system_prompt, usage=None, output="",
                seconds=time.monotonic() - started, error=type(exc).__name__,
                truncated=bool(Distiller._truncation_evidence(exc)))
            raise
        self._rec.note(
            method="async_chat", system_prompt=system_prompt, output=reply, usage=usage,
            seconds=time.monotonic() - started, truncated=False)
        return reply, usage

    def chat_stream(self, system_prompt: str, messages: list[dict],
                    max_tokens: int | None = None, **kw: Any):
        return (yield from self._stream("chat_stream", system_prompt, messages,
                                        max_tokens, **kw))

    def chat_stream_long(self, system_prompt: str, messages: list[dict],
                         max_tokens: int | None = None, **kw: Any):
        return (yield from self._stream("chat_stream_long", system_prompt, messages,
                                        max_tokens, **kw))

    def _stream(self, method: str, system_prompt: str, messages: list[dict],
                max_tokens: int | None, **kw: Any):
        started = time.monotonic()
        parts: list[str] = []
        usage: dict | None = None
        stream = getattr(self._inner, method)(
            system_prompt, messages, max_tokens=max_tokens, **kw)
        try:
            while True:
                try:
                    piece = next(stream)
                except StopIteration as stop:
                    usage = stop.value
                    break
                parts.append(piece)
                yield piece
        except Exception as exc:
            partial = "".join(parts)
            self._rec.note(
                method=method, system_prompt=system_prompt, usage=None, output=partial,
                seconds=time.monotonic() - started, error=type(exc).__name__,
                truncated=bool(Distiller._truncation_evidence(exc, partial)))
            raise
        self._rec.note(
            method=method, system_prompt=system_prompt, output="".join(parts), usage=usage,
            seconds=time.monotonic() - started, truncated=False)
        return usage


class _ReplayDistiller(Distiller):
    """把 `(label, action)` 交给记录器；其余行为与生产蒸馏器一致。"""

    def __init__(self, llm: Any, *, recorder: _Recorder, storage: Any) -> None:
        super().__init__(llm, storage=storage)
        self._recorder = recorder

    def _chat_accounted(self, system_prompt: str, messages: list[dict], label: str,
                        action: str, stream: bool = False,
                        max_tokens: int | None = None) -> tuple[str, bool]:
        self._recorder.annotate(label, action)
        try:
            return super()._chat_accounted(
                system_prompt, messages, label, action, stream=stream, max_tokens=max_tokens)
        finally:
            self._recorder.clear()


# ── 判定 ───────────────────────────────────────────────────────────────

def _phases(draft: dict) -> list[dict]:
    arc = draft.get("character_arc") if isinstance(draft, dict) else None
    return list((arc or {}).get("phases") or [])


def _chapter_no(label: str | None) -> int | None:
    if label == "全书开头" or label == "首章之前":
        return 0
    if not label:
        return None
    match = _CHAPTER_RE.match(label)
    return int(match.group(1)) if match else None


def _start_rows(phases: list[dict], anchors, content: str) -> list[dict]:
    positions = [st.pos for st in anchors.starts
                 if st.k > 1 and st.pos is not None]
    raws = raw_offsets(content, anchors.source_norm, positions) if positions else []
    labels = chapter_labels(content, raws) if raws else []
    out: list[dict] = []
    it = iter(labels)
    for st, phase in zip(anchors.starts, phases):
        if st.k == 1:
            chapter_label, chapter = "全书开头", 0
        elif st.pos is None:
            chapter_label, chapter = "", None
        else:
            chapter_label = next(it)
            chapter = _chapter_no(chapter_label)
        out.append({
            "phase": st.k,
            "label": phase.get("label", "") if isinstance(phase, dict) else "",
            "start_by": st.kind.value,
            "start": st.given,
            "status": st.status.value,
            "chapter": chapter,
            "chapter_label": chapter_label,
            "in_retro": st.status.value == "in_retro",
        })
    return out


def _quote_chapters(quotes: list[str], anchors, content: str) -> list[str]:
    seen: dict[str, None] = {}
    for quote in quotes:
        positions = locate_in_normalized(anchors.source_norm, quote)
        if not positions:
            continue
        raws = raw_offsets(content, anchors.source_norm, positions)
        for label in chapter_labels(content, raws):
            seen.setdefault(label, None)
    return list(seen)


def _entries(model: CardDraft, anchors, content: str) -> list[dict]:
    """`card_from_draft` 送进 `verify` 的每一类条目各一行。

    类别清单与 core 同源：做法 / 记忆、关系态度（`Placement.KEEP_TAGGED`）以及
    `_GENERIC_PATHS`（状态 / 经历类字段）。判定仍只读 `verify` 的结果 ——
    `valid` 为空（只标 0 / 编号非法）单列「无合法标注」。
    """
    count = len(model.character_arc.phases)
    attitude_items = [
        DraftMemory(memory=f"{rel.target}：{attitude.attitude}",
                    occurrences=[DraftOccurrence(phase=attitude.phase,
                                                 quote=attitude.quote)])
        for rel in model.relationships for attitude in rel.attitudes
    ]
    sources = [
        ("situation_behaviors", model.situation_behaviors, "做法",
         REGISTRY["situation_behaviors"].layer, Placement.RELOCATE),
        ("key_memories", model.key_memories, "记忆",
         REGISTRY["key_memories"].layer, Placement.RELOCATE),
        ("relationships.attitudes", attitude_items, "态度",
         REGISTRY["relationships"].layer, Placement.KEEP_TAGGED),
        *((path, get_path(model, path), path, spec.layer, Placement.RELOCATE)
          for path, spec in _GENERIC_PATHS),
    ]
    rows: list[dict] = []
    for field, items, kind, layer, placement in sources:
        if not items:
            continue
        valid = _valid_number_rows(items, count, kind, layer=layer).valid
        result = verify(items, valid, anchors, kind=kind, placement=placement)
        rehung_items = set(result.rehung_items)
        unlocated = set(result.unlocated)
        for i, item in enumerate(items):
            if not valid[i]:
                verdict = "无合法标注"
            elif i in unlocated:
                verdict = "未定位"
            elif i in rehung_items:
                verdict = "改挂"
            else:
                verdict = "一致"
            rows.append({
                "field": field,
                "item": (getattr(item, "situation", "") or getattr(item, "memory", "")
                         or getattr(item, "value", "")),
                "tagged": sorted({occ.phase for occ in item.occurrences}),
                "located": sorted({t for ts in result.tag_targets[i].values() for t in ts}),
                "verdict": verdict,
                "chapters": _quote_chapters(
                    [occ.quote for occ in item.occurrences], anchors, content),
            })
    return rows


def _tsv(rows: list[dict]) -> str:
    def clean(value: Any) -> str:
        return str(value).replace("\t", " ").replace("\n", " ")

    lines = ["field\titem\ttagged\tlocated\tverdict\tchapter"]
    for row in rows:
        lines.append("\t".join([
            clean(row["field"]),
            clean(row["item"]),
            clean(",".join(str(p) for p in row["tagged"])),
            clean(",".join(str(p) for p in row["located"])),
            clean(row["verdict"]),
            clean(",".join(row["chapters"])),
        ]))
    return "\n".join(lines) + "\n"


def _write_calls(path: Path, calls: list[dict], *, stage: str) -> None:
    path.write_text("".join(
        json.dumps({**c, "stage": stage}, ensure_ascii=False) + "\n" for c in calls),
        encoding="utf-8")


def _in_fixed_retro(chapter: int | None) -> bool:
    return chapter is not None and any(lo <= chapter <= hi
                                       for lo, hi in RETRO_CHAPTER_RANGES)


def _checklist(starts: list[dict], anchors, truth_name: str | None) -> list[dict]:
    item1 = "过" if not anchors.reason else "不过"
    detail1 = anchors.reason or "起点齐全、严格递增"

    bad2 = [f"阶段{s['phase']} 起点在第{s['chapter']}章"
            for s in starts
            if _in_fixed_retro(s["chapter"]) or s["chapter"] in IN_CHAPTER_RECALL]
    item2 = "过" if not bad2 else "不过"
    detail2 = "；".join(bad2) if bad2 else "起点都不在回忆区 / 章内回忆"

    # 阶段名每次蒸馏都不同，只能按起点落在哪一章对真值（跟踪文档：阶段对准真实转折）。
    truth = TRUTH.get(truth_name) if truth_name else None
    item3, detail3 = "看不出", "没有与真值表对得上的阶段"
    if truth:
        chapters = [s["chapter"] for s in starts]
        found: list[str] = []
        missing: list[str] = []
        for label, lo, hi in truth.values():
            if any(c is not None and lo <= c <= hi for c in chapters):
                found.append(label)
            else:
                missing.append(f"真值「{label}」{lo}–{hi}：没有阶段起点落在这里")
        item3, detail3 = (("不过", "；".join(missing)) if missing
                          else ("过", "、".join(found)))
        # 全书开头（阶段 1 固定在第 0 章）不是「多出来的阶段」，不列。
        extras = sorted({s["chapter"] for s in starts
                         if s["phase"] > 1 and s["chapter"] is not None
                         and not any(lo <= s["chapter"] <= hi
                                     for _label, lo, hi in truth.values())})
        if extras:
            detail3 += "；多出的起点：" + "、".join(str(c) for c in extras)

    return [
        {"text": ACCEPTANCE_CHECKLIST[0], "verdict": item1, "detail": detail1},
        {"text": ACCEPTANCE_CHECKLIST[1], "verdict": item2, "detail": detail2},
        {"text": ACCEPTANCE_CHECKLIST[2], "verdict": item3, "detail": detail3},
    ]


def _run_verdict(items: list[dict]) -> str:
    verdicts = [item["verdict"] for item in items]
    if "不过" in verdicts:
        return "不过"
    if all(v == "过" for v in verdicts):
        return "过"
    return "看不出"


def _h1(entries: list[dict], count: int) -> dict:
    tagged = Counter(p for row in entries for p in row["tagged"])
    located = Counter(p for row in entries for p in row["located"])
    consistent = sum(1 for row in entries if row["verdict"] == "一致")
    rehung = sum(1 for row in entries if row["verdict"] == "改挂")
    unlocated = sum(1 for row in entries if row["verdict"] == "未定位")
    no_valid_tag = sum(1 for row in entries if row["verdict"] == "无合法标注")
    judged = consistent + rehung
    return {
        "tagged_phase_counts": {str(k): tagged.get(k, 0) for k in range(1, count + 1)},
        "located_phase_counts": {str(k): located.get(k, 0) for k in range(1, count + 1)},
        "consistent": consistent,
        "rehung": rehung,
        "unlocated": unlocated,
        # 只标 0（状态类「全程」/ 经历类「故事开始前」）或编号非法的条目：没有阶段标注可比，
        # 既不算「一致」也不进一致率分母（复审 N3）。
        "no_valid_tag": no_valid_tag,
        "consistency_rate": round(consistent / judged, 4) if judged else None,
    }


def _h5(runs: list[dict]) -> dict:
    truncated = [c for run in runs for c in run["calls_detail"] if c["truncated"]]
    if truncated:
        groups = sorted({c["group"] or c["action"] for c in truncated})
        return {"verdict": "不过", "note": "疑似截断：" + "、".join(groups),
                "truncated_calls": [{"run": c["run"], "n": c["n"],
                                     "group": c["group"], "action": c["action"]}
                                    for c in truncated]}
    return {"verdict": "过", "note": "未命中截断终态", "truncated_calls": []}


# ── 主流程 ─────────────────────────────────────────────────────────────

async def _replay(storage: Any, *, text_id: str, name: str, runs: int,
                  out_dir: Path, text_type: str, truth_name: str | None,
                  prices: dict[str, float | None] | None,
                  user_id: str | None = None) -> dict:
    # 命令行脚本无登录语境：这一次引导读只为拿到属主，随后名单/回忆区都按属主读。
    row = await storage.get_text_unscoped(text_id)
    if row is None:
        raise SystemExit(f"找不到文本 {text_id!r}")
    if user_id and row["user_id"] != user_id:
        raise SystemExit(f"文本 {text_id!r} 不属于用户 {user_id!r}")
    content = row["content"]
    owner_id = row["user_id"]

    # 与生产同一出口（web/deps.py:136）。局部 import 让测试能替换 web.deps.get_user_llm。
    from web.deps import get_user_llm
    llm = await get_user_llm(owner_id, storage=storage)
    if llm is None:
        raise SystemExit(
            f"属主未配置 LLM（user_id={owner_id!r}）；生产蒸馏只认属主自己的 key，不回落全局")

    setup = _Recorder()
    setup_distiller = _ReplayDistiller(_RecordingLLM(llm, setup), recorder=setup,
                                       storage=storage)
    chars = await resolve_characters(storage, setup_distiller, text_id, owner_id, content)
    if not name:
        from core.character_roster import target_character_name
        name = target_character_name(chars)
    aliases = aliases_for(chars, name)
    retro = await resolve_retro_regions(storage, setup_distiller, text_id, owner_id, content)

    out_dir.mkdir(parents=True, exist_ok=True)
    # 名单 / 回忆区是整本书的属性，整场只调一次：记在 run 之外，标 stage=setup（F3）。
    _write_calls(out_dir / "calls.jsonl", setup.calls, stage="setup")
    run_summaries: list[dict] = []

    for n in range(1, runs + 1):
        run_dir = out_dir / f"run-{n}"
        run_dir.mkdir(parents=True, exist_ok=True)
        recorder = _Recorder()
        distiller = _ReplayDistiller(_RecordingLLM(llm, recorder), recorder=recorder,
                                     storage=storage)

        frames = await asyncio.to_thread(
            lambda: list(distiller.distill_incremental_stream(
                content, name, aliases=aliases, text_type=text_type, retro=retro)))
        error = next((f["error"] for f in frames
                      if isinstance(f, dict) and "error" in f), None)
        if error:
            raise RuntimeError(f"run-{n} 蒸馏失败：{error}")
        draft_text = next((f for f in reversed(frames) if isinstance(f, str)), None)
        if draft_text is None:
            raise RuntimeError(f"run-{n} 没有交出草稿")
        draft = loads_llm_json(draft_text)

        anchors = build_anchors(_phases(draft), content, retro=retro.spans(content))
        model = CardDraft.model_validate(draft)
        phase_rows = _start_rows(_phases(draft), anchors, content)
        entry_rows = _entries(model, anchors, content)
        await asyncio.to_thread(card_from_draft, draft, content, retro=retro)

        _write_calls(run_dir / "calls.jsonl", recorder.calls, stage="run")
        (run_dir / "draft.json").write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "retro.json").write_text(retro.to_json(), encoding="utf-8")
        (run_dir / "phase_starts.json").write_text(
            json.dumps({"phases": phase_rows},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "entries.tsv").write_text(_tsv(entry_rows), encoding="utf-8")

        calls_detail = [{**c, "run": n} for c in recorder.calls]
        items = _checklist(phase_rows, anchors, truth_name)
        run_summaries.append({
            "run": n,
            "calls": len(recorder.calls),
            "calls_detail": calls_detail,
            "phases": phase_rows,
            "entries": entry_rows,
            "H1": _h1(entry_rows, len(model.character_arc.phases)),
            "H2": {"verdict": _run_verdict(items), "items": items},
        })

    phase_counts = [len(r["phases"]) for r in run_summaries]
    starts = [[p["chapter"] for p in r["phases"]] for r in run_summaries]
    h2_runs = [{"run": r["run"], "verdict": r["H2"]["verdict"], "items": r["H2"]["items"]}
               for r in run_summaries]
    h2_overall = ("过" if all(r["verdict"] == "过" for r in h2_runs)
                  else "不过" if any(r["verdict"] == "不过" for r in h2_runs) else "看不出")

    all_calls = setup.calls + [c for r in run_summaries for c in r["calls_detail"]]
    known = [c["usage"] for c in all_calls if c["usage"]]
    unknown = len(all_calls) - len(known)
    usage = {
        "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in known),
        CACHE_HIT_FIELD: sum(u.get(CACHE_HIT_FIELD) or 0 for u in known),
        CACHE_MISS_FIELD: sum(u.get(CACHE_MISS_FIELD) or 0 for u in known),
        "completion_tokens": sum(u.get("completion_tokens") or 0 for u in known),
        "calls": len(all_calls),
        # F6：拿不到用量的调用（截断 / 失败）单列，成本是**下界**，不把它们当 0 混进去。
        "unknown_usage_calls": unknown,
        "note": (f"{unknown} 次调用用量未知（截断或调用失败），未计入成本"
                 if unknown else ""),
    }

    cost = None
    if prices and all(v is not None for v in prices.values()):
        hit = usage[CACHE_HIT_FIELD]
        miss = usage[CACHE_MISS_FIELD] or max(usage["prompt_tokens"] - hit, 0)
        cost = round(
            hit / 1e6 * prices["cache_hit"]
            + miss / 1e6 * prices["cache_miss"]
            + usage["completion_tokens"] / 1e6 * prices["output"], 6)

    summary = {
        "text_id": text_id,
        "name": name,
        "truth": truth_name,
        # 实际在跟谁说话（F5）：只记模型与地址，**不记 key**。
        "model": str(getattr(llm, "model", "") or ""),
        "base_url": str(getattr(llm, "base_url", "") or ""),
        "runs": [{"run": r["run"], "calls": r["calls"]} for r in run_summaries],
        "setup_calls": len(setup.calls),
        "H1": {"runs": [{"run": r["run"], **r["H1"]} for r in run_summaries]},
        "H2": {"verdict": h2_overall, "runs": h2_runs},
        "H3": {"phase_counts": phase_counts, "starts": starts,
               "stable": len({tuple(s) for s in starts}) <= 1
               and len(set(phase_counts)) <= 1},
        "H5": _h5(run_summaries),
        "H6": {"runs": []},
        "usage": usage,
        "cost": cost,
    }
    for r in run_summaries:
        hits = [f"起点 阶段{p['phase']}（{p['chapter_label']}）"
                for p in r["phases"] if p["chapter"] in IN_CHAPTER_RECALL]
        hits += [f"条目 {row['field']}「{row['item']}」" for row in r["entries"]
                 if any(_chapter_no(label) in IN_CHAPTER_RECALL for label in row["chapters"])]
        summary["H6"]["runs"].append({"run": r["run"], "hits": hits})

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_table(summary)

    return summary


def _print_table(summary: dict) -> None:
    print(f"text={summary['text_id']}  name={summary['name']}  "
          f"runs={len(summary['runs'])}  model={summary['model']}  "
          f"base_url={summary['base_url']}")
    for run in summary["runs"]:
        print(f"  run {run['run']}: calls={run['calls']}")
    print(f"H1 一致率/分布（只出数据）: {json.dumps(summary['H1']['runs'], ensure_ascii=False)}")
    print(f"H2 起点对准真实转折: {summary['H2']['verdict']}")
    for run in summary["H2"]["runs"]:
        print(f"  run {run['run']}: {run['verdict']}")
    print(f"H3 两次稳定: {summary['H3']['phase_counts']} / {summary['H3']['starts']}"
          f" stable={summary['H3']['stable']}")
    print(f"H5 8192 上限: {summary['H5']['verdict']}（{summary['H5']['note']}）")
    print(f"H6 章内回忆: {summary['H6']['runs']}")
    print(f"usage: {summary['usage']}  cost={summary['cost']}")


async def run_replay(storage: Any, *, text_id: str, name: str = "", runs: int = 1,
                     out_dir: str | Path, text_type: str = "story",
                     truth_name: str | None = None,
                     prices: dict[str, float | None] | None = None,
                     user_id: str | None = None) -> dict:
    """重放入口。整段包在 `system_llm_context()`：用量不进 `usage_stats`。"""
    with system_llm_context():
        return await _replay(storage, text_id=text_id, name=name, runs=runs,
                             out_dir=Path(out_dir), text_type=text_type,
                             truth_name=truth_name, prices=prices,
                             user_id=user_id)


# ── CLI ────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="蒸馏小重放（不存卡、不记 usage）")
    parser.add_argument("--text-id", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out", required=True)
    parser.add_argument("--truth", default=None)
    parser.add_argument("--text-type", default="story")
    parser.add_argument("--user-id", default=None,
                        help="属主校验；文本不属于该用户时拒绝重放")
    parser.add_argument("--price-cache-hit", type=float, default=None,
                        help="每百万缓存命中输入 token 单价（不写死在代码里）")
    parser.add_argument("--price-cache-miss", type=float, default=None,
                        help="每百万缓存未命中输入 token 单价")
    parser.add_argument("--price-output", type=float, default=None,
                        help="每百万输出 token 单价")
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> dict:
    from web.deps import get_storage

    storage = get_storage()
    try:
        return await run_replay(
            storage, text_id=args.text_id, name=args.name, runs=args.runs,
            out_dir=args.out, text_type=args.text_type, truth_name=args.truth,
            prices={
                "cache_hit": args.price_cache_hit,
                "cache_miss": args.price_cache_miss,
                "output": args.price_output,
            },
            user_id=args.user_id)
    finally:
        await storage.close()


def main(argv: list[str] | None = None) -> None:
    asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    main()
