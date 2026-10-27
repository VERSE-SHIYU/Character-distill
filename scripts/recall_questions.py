#!/usr/bin/env python3
"""出题命令行（0d）：``probe`` / ``generate`` / ``multiturn``。

- ``probe``：人工题（剔除寒暄）过指定的模型关（A.5 / A.6）各两次，只量误判率与
  两次一致率，不改任何题。
- ``generate``（spec ⑱）：一条命令跑完「锚点 → 起草 → g_ground → A.5 → A.6 →
  修两轮」，中途不停；起草与判定都用 DeepSeek。
- ``multiturn``：读已出好的题库，把问句里点了名单中他人名的题改成两轮（上文点名、
  问句只用代词），答案片段原样沿用，过关 1（确定性）与 A.6 后写新题库。

关卡规则全在 `core.recall_synth`（本文件不判通过条件），模型 JSON 解析走
`core.llm_json.loads_llm_json`，照抄的提示词只从本地 `prompts/` 读并逐份核哈希；
起草提示词 `DRAFT_PROMPT` 在本仓库（⑱：论文没给）。
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import concurrency as C  # noqa: E402
from core import recall_synth  # noqa: E402
from core.llm_json import loads_llm_json  # noqa: E402
from core.quotes import normalize  # noqa: E402
from core.request_context import system_llm_context  # noqa: E402
from eval_recall import Book, EvalAbort, load_book, load_cases, load_suite  # noqa: E402

PROMPT_FILES: dict[str, str] = {
    "own_words_A5": "own_words_A5.txt",
    "verifier_A6": "verifier_A6.txt",
    "intent_factual_A3": "intent_factual_A3.txt",
}

MAX_TOKENS = 512
# 模板都是完整指令；补一句用户轮让厂商拿到非空 user 消息。
USER_TURN = "Proceed with the judgment."

# 锚点并发数：8 路 `ThreadPoolExecutor` + `C.ctx_submit`（本脚本自定；`core/distiller.py` 没有现成线程池可沿用）。
DRAFT_WORKERS = 8

# ⑲：一律只用本名找锚点；林黛玉本名只出现 284 次，原文主要叫「黛玉」（1375 次）。
ANCHOR_NAME_OVERRIDES: dict[str, list[str]] = {"林黛玉": ["黛玉"]}


def _stem(book: Book) -> str:
    """产物文件名 `{text_id}_{角色名}`（如 `t1_甲.jsonl`）；不作集合名 —— 评测集合名见 `eval_recall.run_suite`。"""
    return f"{book.text_id}_{book.character}"


def _suite_row(book: Book) -> dict:
    """`suite.json` 的一行；直接调用 `run_generate` 未传书目行时，由书对象补出同形一行。"""
    return {
        "book": book.book,
        "title": book.title,
        "user_id": book.user_id,
        "text_id": book.text_id,
        "character": book.character,
        "cases": book.cases,
        "card_id": book.card_id,
    }


def _new_usage() -> dict[str, int]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def _add_usage(total: dict[str, int], usage: Any) -> None:
    total["calls"] += 1
    if isinstance(usage, dict):
        total["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        total["completion_tokens"] += int(usage.get("completion_tokens") or 0)


def _ask_once(llm: Any, prompt: str, usage: dict[str, int]) -> dict | None:
    """调一次模型并解析 JSON；解析不了返回 None（由调用方决定重调）。"""
    text = llm.chat(prompt, [{"role": "user", "content": USER_TURN}], max_tokens=MAX_TOKENS)
    _add_usage(usage, getattr(llm, "last_usage", None))
    try:
        value = loads_llm_json(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _ask_gate(llm: Any, prompt: str, parse, usage: dict[str, int]) -> dict | None:
    """解析不了（含结构不对）重调一次；仍不行 → None，调用方记 ``discarded:parse``。"""
    for _ in range(2):
        value = _ask_once(llm, prompt, usage)
        if value is not None and parse(value) is not None:
            return value
    return None


def _parse_discard(draft: dict, attempt: int) -> dict:
    return {
        "id": draft.get("id"),
        "status": "discarded",
        "gate": "parse",
        "problems": ["模型结果两次都解析不了"],
        "own_words_flag": False,
        "attempt": attempt,
    }


def _write_jsonl(path: str | Path, rows: list[dict]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: str | Path, payload: Any) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load_prompts(prompt_dir: str | Path) -> dict[str, str]:
    """读 3 份照抄的提示词并逐份核哈希；按字节解码，不做换行转换。"""
    base = Path(prompt_dir)
    prompts: dict[str, str] = {}
    for name, filename in PROMPT_FILES.items():
        path = base / filename
        try:
            text = path.read_bytes().decode("utf-8")
        except OSError as exc:
            raise EvalAbort(f"读不了提示词 {path}：{exc}") from exc
        try:
            recall_synth.check_prompt(name, text)
        except ValueError as exc:
            raise EvalAbort(str(exc)) from exc
        prompts[name] = text
    return prompts


PROBE_GATES = ("a5", "a6")


def _a5_once(llm: Any, prompts: dict[str, str], case: dict,
             usage: dict[str, int]) -> dict | None:
    prompt = recall_synth.render(
        "own_words_A5", prompts["own_words_A5"],
        question=case["query"], quotes=recall_synth.quotes_block(case.get("should") or []))
    return _ask_once(llm, prompt, usage)


def _verifier_prompt(prompts: dict[str, str], question: str, should: list[str]) -> str:
    """A.6 提示词的唯一拼法：问句 + 证据块 + 表 4 意图槽。"""
    return recall_synth.render(
        "verifier_A6", prompts["verifier_A6"],
        question=question, quotes=recall_synth.quotes_block(should),
        **recall_synth.intent_slots(prompts["intent_factual_A3"]))


def _a6_once(llm: Any, prompts: dict[str, str], case: dict,
             usage: dict[str, int]) -> dict | None:
    return _ask_once(llm, _verifier_prompt(prompts, case["query"], case.get("should") or []),
                     usage)


def _a6_flags(raws: list[dict | None]) -> dict[str, int]:
    """A.6 各项判 False（leak 判 True）的次数 —— 误判按核查项分开报（spec ⑰）。"""
    flags = dict.fromkeys(
        ("answerable_false", "sufficient_false", "per_doc_false", "leak_true",
         "intent_match_false"), 0)
    for value in raws:
        if not isinstance(value, dict):
            continue
        for field, key in (("answerable", "answerable_false"),
                           ("sufficient", "sufficient_false")):
            if value.get(field) is False:
                flags[key] += 1
        per_doc = value.get("per_doc")
        if isinstance(per_doc, dict) and any(v is False for v in per_doc.values()):
            flags["per_doc_false"] += 1
        if value.get("leak") is True:
            flags["leak_true"] += 1
        if value.get("intent_match") is False:
            flags["intent_match_false"] += 1
    return flags


def _agreement(rows: list[dict], key: str) -> float | None:
    pairs = len(rows)
    if not pairs:
        return None
    agreed = sum(1 for row in rows
                 if row[key][0] is not None and row[key][0] == row[key][1])
    return agreed / pairs


def run_probe(books: list[Book], llm: Any, prompts: dict[str, str],
              out_path: str | Path, gates: tuple[str, ...] = PROBE_GATES) -> dict:
    """摸底：每条人工题过指定模型关各两次，只记录结论，不改题。

    寒暄（`should` 为空）不量模型关，单独列出 id（spec ⑮）。
    """
    unknown = set(gates) - set(PROBE_GATES)
    if unknown:
        raise EvalAbort(f"未知的摸底关：{sorted(unknown)}")

    usage = _new_usage()
    rows: list[dict] = []
    excluded: list[Any] = []
    for book in books:
        if not book.cases:
            continue
        for case in load_cases(book.cases):
            if not case.get("should"):
                excluded.append(case.get("id"))
                continue
            row: dict[str, Any] = {"book": book.book, "id": case.get("id"),
                                   "question": case["query"]}
            if "a5" in gates:
                raw = [_a5_once(llm, prompts, case, usage) for _ in range(2)]
                row["a5"] = raw
                row["a5_ok"] = [recall_synth.critic_ok(v) for v in raw]
            if "a6" in gates:
                raw = [_a6_once(llm, prompts, case, usage) for _ in range(2)]
                row["a6"] = raw
                evidence_ids = recall_synth.evidence_ids(case["should"])
                row["a6_ok"] = [recall_synth.verify_ok(v, evidence_ids) for v in raw]
            rows.append(row)

    summary: dict[str, Any] = {
        "n": len(rows),
        "excluded_no_should": excluded,
        "gates": list(gates),
        "usage": usage,
        "per_query": rows,
    }
    if "a5" in gates:
        a5_ok = [v for row in rows for v in row["a5_ok"]]
        summary["a5"] = {
            "calls": len(a5_ok),
            "searchable_true": sum(1 for v in a5_ok if v is True),
            "unparsed": sum(1 for v in a5_ok if v is None),
            "agree_rate": _agreement(rows, "a5_ok"),
        }
    if "a6" in gates:
        a6_ok = [v for row in rows for v in row["a6_ok"]]
        summary["a6"] = {
            "calls": len(a6_ok),
            "fail": sum(1 for v in a6_ok if v is False),
            "unparsed": sum(1 for v in a6_ok if v is None),
            **_a6_flags([v for row in rows for v in row["a6"]]),
            "agree_rate": _agreement(rows, "a6_ok"),
        }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(out_path, summary)
    return summary


def _anchor_names(book: Book) -> list[str]:
    """⑲：找锚点只用本名，林黛玉是唯一例外（原文主要叫「黛玉」）。"""
    return ANCHOR_NAME_OVERRIDES.get(book.character, [book.character])


def _draft_shape(value: dict) -> dict | None:
    """起草结果的结构：``{"skip": true}`` 或 ``{"query", "should"}``；其余算未解析。"""
    if value.get("skip") is True:
        return value
    query = value.get("query")
    should = value.get("should")
    if isinstance(should, str):
        should = [should]
    if not isinstance(query, str) or not query.strip():
        return None
    if not isinstance(should, list) or not should or not all(isinstance(s, str) for s in should):
        return None
    return {"query": query, "should": should}


def _feedback(problems: list[str]) -> str:
    if not problems:
        return ""
    return ("\nPrevious attempt was rejected. Fix these problems:\n"
            + "\n".join(f"- {p}" for p in problems) + "\n")


def _run_gates(
    book: Book, draft: dict, anchor: dict, source_norm: str, llm: Any,
    prompts: dict[str, str], usage: dict[str, int], attempt: int,
) -> tuple[dict, dict]:
    """按关卡顺序调模型，结果交给 `judge` 判；关 1 不过不花任何模型调用。"""
    question = draft["query"]
    should = draft.get("should") or []
    quotes = recall_synth.quotes_block(should)
    raws: dict[str, Any] = {"critic": None, "verdict": None}

    result = recall_synth.judge(draft, anchor, source_norm, attempt=attempt)
    if result["status"] != "critic_unparsed":
        return result, raws

    critic_prompt = recall_synth.render(
        "own_words_A5", prompts["own_words_A5"], question=question, quotes=quotes)
    critic = _ask_gate(llm, critic_prompt, recall_synth.critic_ok, usage)
    if critic is None:
        return _parse_discard(draft, attempt), raws
    raws["critic"] = critic

    result = recall_synth.judge(
        draft, anchor, source_norm, critic=critic, attempt=attempt)
    if result["status"] != "verify_unparsed":
        return result, raws

    evidence_ids = recall_synth.evidence_ids(should)
    verdict = _ask_gate(
        llm, _verifier_prompt(prompts, question, should),
        lambda value: recall_synth.verify_ok(value, evidence_ids), usage)
    if verdict is None:
        return _parse_discard(draft, attempt), raws
    raws["verdict"] = verdict

    return recall_synth.judge(
        draft, anchor, source_norm, critic=critic,
        verdict=verdict, attempt=attempt), raws


def _one_anchor(
    book: Book, anchor: dict, anchor_id: str, source_norm: str,
    llm: Any, prompts: dict[str, str],
) -> dict:
    """一个锚点：起草 → g_ground → A.5 → A.6；未过把原因填回反馈位重写（至多 3 次）。

    每个锚点一份适配器副本：``last_usage`` 是跨调用的共享槽，并发读会串号
    （``core/distiller.py`` 缺陷 20 同形）。副本共享底层客户端，只是各记各的账。
    """
    worker_llm = copy.copy(llm)
    usage = _new_usage()
    history: list[dict] = []
    feedback = ""

    for attempt in range(1, recall_synth.MAX_ATTEMPTS + 1):
        prompt = recall_synth.render(
            "draft", prompts["draft"], character=book.character, title=book.title,
            window=anchor["text"], feedback=feedback)
        drafted = _ask_gate(worker_llm, prompt, _draft_shape, usage)
        if drafted is None:
            history.append(_parse_discard({"id": anchor_id}, attempt))
            return {"id": anchor_id, "status": "discarded", "gate": "parse",
                    "case": None, "attempts": history, "usage": usage}
        if drafted.get("skip") is True:
            return {"id": anchor_id, "status": "skip", "case": None,
                    "attempts": [], "usage": usage}

        draft = {"id": anchor_id, "query": drafted["query"], "should": drafted["should"]}
        result, raws = _run_gates(
            book, draft, anchor, source_norm, worker_llm, prompts, usage, attempt)
        result = {**result, "raw": raws, "query": draft["query"], "should": draft["should"]}
        history.append(result)
        if result["status"] == "passed":
            return {"id": anchor_id, "status": "passed", "attempts": history,
                    "usage": usage, "case": {
                        "id": anchor_id,
                        "phase": None,
                        "query": draft["query"],
                        "should": draft["should"],
                        "should_not": [],
                        "own_words_flag": result["own_words_flag"],
                    }}
        if result["status"] == "discarded":
            return {"id": anchor_id, "status": "discarded", "gate": result.get("gate"),
                    "case": None, "attempts": history, "usage": usage}
        feedback = _feedback(result["problems"])

    raise AssertionError("judge 未在 MAX_ATTEMPTS 内给出终态")


def _repair_passes(
    outcomes: list[dict], gates: tuple[str, ...] = ("ground", "critic", "verify"),
) -> dict[str, dict[str, int]]:
    """各关退回后「修 1 / 2 次通过」的条数，按最后一次退回的关分桶。"""
    counts = {str(n): dict.fromkeys(gates, 0)
              for n in range(1, recall_synth.MAX_ATTEMPTS)}
    for outcome in outcomes:
        history = outcome["attempts"]
        if outcome["status"] != "passed" or len(history) < 2:
            continue
        repairs = str(len(history) - 1)
        gate = history[-2].get("gate")
        if repairs in counts and gate in counts[repairs]:
            counts[repairs][gate] += 1
    return counts


def run_generate(
    books: list[Book], llm: Any, prompts: dict[str, str],
    out_dir: str | Path, *, k: int = 30, workers: int = DRAFT_WORKERS,
    suite_rows: list[dict] | None = None,
) -> dict:
    """⑱：锚点 → 起草 → 三关 → 修两轮，一条命令跑完，中途不停。

    结束时在 out_dir 写 `suite_eval.json`：生成行 `cases` 指向写出的题文件，人工题把
    原文件拷进 out_dir；全部相对 out_dir，可直接 `eval_recall --suite`。
    `suite_rows` 是 `load_suite` 的原始行，不传时由 `books` 补出同形行。
    """
    started = time.monotonic()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    source_rows = suite_rows if suite_rows is not None else [_suite_row(book) for book in books]
    if len(source_rows) != len(books):
        raise EvalAbort(f"书目行数与书对象数不一致：{len(source_rows)} != {len(books)}")
    rows = [dict(row) for row in source_rows]
    usage = _new_usage()
    verdict_rows: list[dict] = []
    summary: dict[str, Any] = {"books": {}}
    empty_indices: set[int] = set()

    for index, book in enumerate(books):
        if book.cases:  # 祈灼：43 条人工题直接进套件（spec §5 第 8 条）
            continue
        stem = _stem(book)
        source_norm = normalize(book.source)
        anchors = [{"id": f"a{i:02d}", **a} for i, a in enumerate(
            recall_synth.sample_anchors(book.source, _anchor_names(book), k=k), 1)]
        _write_json(out / f"{stem}.anchors.json", {
            "book": book.book,
            "title": book.title,
            "text_id": book.text_id,
            "character": book.character,
            "anchor_names": _anchor_names(book),
            "anchors": anchors,
        })

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                C.ctx_submit(pool, _one_anchor, book, anchor, anchor["id"],
                             source_norm, llm, prompts)
                for anchor in anchors
            ]
            outcomes = [future.result() for future in futures]

        problems = recall_synth.ledger_problems(
            [outcome["id"] for outcome in outcomes if outcome["status"] != "skip"],
            [anchor["id"] for anchor in anchors])
        if problems:
            raise EvalAbort("台账未通过，整批拒收：\n" + "\n".join(problems))

        cases = [outcome["case"] for outcome in outcomes if outcome["case"]]
        _write_jsonl(out / f"{stem}.jsonl", cases)
        if not cases:
            empty_indices.add(index)
        for outcome in outcomes:
            for key, value in outcome["usage"].items():
                usage[key] += value
            for result in outcome["attempts"]:
                verdict_rows.append(
                    {"book": book.book, "character": book.character, **result})

        finals = [
            {"status": outcome["status"], "gate": outcome.get("gate"),
             "own_words_flag": bool(outcome["case"] and outcome["case"]["own_words_flag"])}
            for outcome in outcomes if outcome["status"] != "skip"
        ]
        summary["books"][stem] = {
            "anchors": len(anchors),
            "skip": sum(1 for outcome in outcomes if outcome["status"] == "skip"),
            "outcomes": recall_synth.outcome_counts(finals),
            "repair_passes": _repair_passes(outcomes),
            "overlap": recall_synth.overlap_histogram(
                recall_synth.lexical_overlap(case["query"], case["should"])
                for case in cases),
        }

    for book in books:
        if not book.cases:
            continue
        cases = load_cases(book.cases)
        summary[f"{book.book}（人工题）"] = {
            "n": len(cases),
            "overlap": recall_synth.overlap_histogram(
                recall_synth.lexical_overlap(case["query"], case.get("should") or [])
                for case in cases),
        }

    suite: list[dict] = []
    for index, (book, row) in enumerate(zip(books, rows)):
        if book.cases:
            source = Path(book.cases)
            target = out / source.name
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            row["cases"] = source.name
        elif index in empty_indices:
            continue
        else:
            row["cases"] = f"{_stem(book)}.jsonl"
        suite.append(row)
    _write_json(out / "suite_eval.json", suite)
    summary["omitted_roles"] = [_stem(books[i]) for i in sorted(empty_indices)]

    _write_jsonl(out / "verdicts.jsonl", verdict_rows)
    summary["usage"] = usage
    summary["duration_s"] = round(time.monotonic() - started, 1)
    _write_json(out / "summary.json", summary)
    return summary


MULTITURN_GATES = ("multiturn", "verify")


def _turns_shape(value: dict) -> dict | None:
    """改写结果的结构：``{"turn1", "turn2"}`` 两个非空字符串；其余算未解析。"""
    turns = {key: value.get(key) for key in ("turn1", "turn2")}
    if not all(isinstance(v, str) and v.strip() for v in turns.values()):
        return None
    return turns


def _one_multiturn(
    book: Book, case: dict, entity: str, names: list[str],
    llm: Any, prompts: dict[str, str],
) -> dict:
    """一条题：改写 → 关 1 → A.6；不过把原因填回反馈位重写（共 MAX_ATTEMPTS 次），仍不过丢弃。

    适配器副本的理由同 `_one_anchor`（``last_usage`` 是共享槽）。
    """
    worker_llm = copy.copy(llm)
    usage = _new_usage()
    case_id = f"{case['id']}_mt"
    should = case.get("should") or []
    evidence_ids = recall_synth.evidence_ids(should)
    history: list[dict] = []
    feedback = ""

    def done(status: str, gate: str | None, new_case: dict | None = None) -> dict:
        return {"id": case_id, "source_id": case["id"], "status": status, "gate": gate,
                "case": new_case, "attempts": history, "usage": usage}

    for attempt in range(1, recall_synth.MAX_ATTEMPTS + 1):
        prompt = recall_synth.render(
            "multiturn", prompts["multiturn"], character=book.character, title=book.title,
            question=case["query"], entity=entity, feedback=feedback)
        turns = _ask_gate(worker_llm, prompt, _turns_shape, usage)
        if turns is None:
            history.append(_parse_discard({"id": case_id}, attempt))
            return done("discarded", "parse")
        turn1, turn2 = turns["turn1"], turns["turn2"]
        record = {"id": case_id, "source_id": case["id"], "entity": entity,
                  "attempt": attempt, "turn1": turn1, "turn2": turn2, "raw": None}

        gate = "multiturn"
        problems = recall_synth.multiturn_problems(turn1, turn2, names)
        if not problems:
            gate = "verify"
            # A.6 没有上文占位：上文按 `multiturn_question` 的约定拼进 {question}。
            verdict = _ask_gate(
                worker_llm,
                _verifier_prompt(prompts, recall_synth.multiturn_question(turn1, turn2), should),
                lambda value: recall_synth.verify_ok(value, evidence_ids), usage)
            if verdict is None:
                history.append(_parse_discard({"id": case_id}, attempt))
                return done("discarded", "parse")
            record["raw"] = verdict
            if recall_synth.verify_ok(verdict, evidence_ids):
                history.append({**record, "status": "passed", "gate": None, "problems": []})
                return done("passed", None, {
                    **case, "id": case_id, "query": turn2,
                    "history": [{"role": "user", "content": turn1}],
                    "source_id": case["id"], "entity": entity})
            problems = [verdict["notes"]] if verdict["notes"].strip() else []

        final = attempt >= recall_synth.MAX_ATTEMPTS
        history.append({**record, "status": "discarded" if final else f"{gate}_fail",
                        "gate": gate, "problems": problems})
        if final:
            return done("discarded", gate)
        feedback = _feedback(problems)

    raise AssertionError("未在 MAX_ATTEMPTS 内给出终态")


def run_multiturn(
    books: list[Book], rosters: list[list[dict]], llm: Any, prompts: dict[str, str],
    out_dir: str | Path, *, workers: int = DRAFT_WORKERS,
    suite_rows: list[dict] | None = None,
) -> dict:
    """已出好的题库 → 两轮题库（答案片段原样沿用）；丢弃的不补题。

    ``rosters[i]`` 是 ``books[i]`` 那本书的角色名单（``cached_characters`` 的行）。
    写 `<stem>.multiturn.jsonl`、`suite_eval.json`（相对 out_dir，可直接 `eval_recall --suite`）、
    `verdicts.jsonl`、`summary.json`。
    """
    started = time.monotonic()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    source_rows = suite_rows if suite_rows is not None else [_suite_row(book) for book in books]
    if not (len(source_rows) == len(books) == len(rosters)):
        raise EvalAbort(f"书目行 / 书对象 / 名单数不一致：{len(source_rows)} / {len(books)} / {len(rosters)}")
    usage = _new_usage()
    verdict_rows: list[dict] = []
    summary: dict[str, Any] = {"books": {}}
    suite: list[dict] = []

    for book, row, roster in zip(books, source_rows, rosters):
        if not book.cases:
            continue
        stem = _stem(book)
        picks = []
        for case in load_cases(book.cases):
            if not case.get("should"):  # 寒暄没有证据片段，A.6 无从核
                continue
            found = recall_synth.multiturn_entity(case["query"], roster, book.character)
            if found:
                picks.append((case, *found))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [C.ctx_submit(pool, _one_multiturn, book, case, entity, names, llm, prompts)
                       for case, entity, names in picks]
            outcomes = [future.result() for future in futures]

        cases = [outcome["case"] for outcome in outcomes if outcome["case"]]
        _write_jsonl(out / f"{stem}.multiturn.jsonl", cases)
        if cases:
            suite.append({**row, "cases": f"{stem}.multiturn.jsonl"})

        book_usage = _new_usage()
        for outcome in outcomes:
            for key, value in outcome["usage"].items():
                book_usage[key] += value
                usage[key] += value
            for result in outcome["attempts"]:
                verdict_rows.append({"book": book.book, "character": book.character, **result})
        discarded: dict[str, int] = {}
        for outcome in outcomes:
            if outcome["status"] == "discarded":
                discarded[outcome["gate"]] = discarded.get(outcome["gate"], 0) + 1
        summary["books"][stem] = {
            "candidates": len(picks),
            "passed": len(cases),
            "discarded": discarded,
            "repair_passes": _repair_passes(outcomes, MULTITURN_GATES),
            "usage": book_usage,
        }

    _write_json(out / "suite_eval.json", suite)
    _write_jsonl(out / "verdicts.jsonl", verdict_rows)
    summary["usage"] = usage
    summary["duration_s"] = round(time.monotonic() - started, 1)
    _write_json(out / "summary.json", summary)
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="0d 出题工具（probe / generate / multiturn）")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="人工题过指定模型关各两次（不改题）")
    probe.add_argument("--suite", required=True)
    probe.add_argument("--prompts", required=True)
    probe.add_argument("--out", required=True)
    probe.add_argument("--gates", nargs="+", choices=PROBE_GATES, default=list(PROBE_GATES))

    generate = sub.add_parser("generate", help="锚点 → 起草 → 三关 → 修两轮（⑱）")
    generate.add_argument("--suite", required=True)
    generate.add_argument("--prompts", required=True)
    generate.add_argument("--out-dir", dest="out_dir", required=True)
    generate.add_argument("--k", type=int, default=30)

    multiturn = sub.add_parser("multiturn", help="已有题库 → 两轮题（关 1 + A.6，修两轮）")
    multiturn.add_argument("--suite", required=True)
    multiturn.add_argument("--prompts", required=True)
    multiturn.add_argument("--out-dir", dest="out_dir", required=True)
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> None:
    from core.character_roster import cached_characters
    from web.deps import get_storage, get_user_llm

    entries = load_suite(args.suite)
    storage = get_storage()
    try:
        with system_llm_context():
            books = [await load_book(storage, entry) for entry in entries]
            prompts = _load_prompts(args.prompts)
            if args.command == "generate":
                prompts["draft"] = recall_synth.DRAFT_PROMPT
            elif args.command == "multiturn":
                prompts["multiturn"] = recall_synth.MULTITURN_PROMPT
                # load_book 已核过名单非空；此处取同一份缓存给选题用。
                rosters = [await cached_characters(storage, b.text_id, b.user_id) or []
                           for b in books]
            user_ids = sorted({book.user_id for book in books})
            if len(user_ids) != 1:
                raise EvalAbort(f"书目跨多个用户：{user_ids}（本次只取一个用户的 key）")
            llm = await get_user_llm(user_ids[0], storage)
            if llm is None:
                raise EvalAbort("该用户没有自己的 LLM key（模型关只走用户自己的 key）")
            if args.command == "probe":
                summary = run_probe(books, llm, prompts, args.out, tuple(args.gates))
            elif args.command == "multiturn":
                summary = run_multiturn(books, rosters, llm, prompts, args.out_dir,
                                        suite_rows=entries)
            else:
                summary = run_generate(books, llm, prompts, args.out_dir, k=args.k,
                                       suite_rows=entries)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        await storage.close()


def main(argv: list[str] | None = None) -> None:
    try:
        asyncio.run(_main(_parse_args(argv)))
    except EvalAbort as exc:
        print(f"[recall_questions] {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
