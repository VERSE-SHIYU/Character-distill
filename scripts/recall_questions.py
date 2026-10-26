#!/usr/bin/env python3
"""出题命令行（0d）：``probe`` / ``anchors`` / ``check``。

- ``probe``：人工题（剔除寒暄）过指定的模型关（A.5 / A.6）各两次，只量误判率与
  两次一致率，不改任何题。
- ``anchors``：按全书均分的 60 段抽锚点，每段至多一个（`sample_anchors`）。
- ``check``：`ledger_problems`（有问题整批拒收）→ g_ground → A.4 → A.5 → A.6，
  每一步只对上一步过了的调；写 verdicts / repair / 评测格式的题。

关卡规则全在 `core.recall_synth`（本文件不判通过条件），模型 JSON 解析走
`core.llm_json.loads_llm_json`，提示词只从本地 `prompts/` 读并逐份核哈希。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def _stem(book: Book) -> str:
    """产物文件名（集合名也用它）：编号不用中文名（Chroma 集合名限制，0e 前核实）。"""
    return f"{book.text_id}_{book.character}"


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


def _read_jsonl(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _write_jsonl(path: str | Path, rows: list[dict]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: str | Path, payload: Any) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load_str_list(path: str | Path, default: list[str]) -> list[str]:
    path = Path(path)
    if not path.exists():
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    return [str(v) for v in value]


def _load_prompts(prompt_dir: str | Path) -> dict[str, str]:
    """读 4 份照抄的提示词并逐份核哈希；按字节解码，不做换行转换。"""
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


def _a6_once(llm: Any, prompts: dict[str, str], case: dict,
             usage: dict[str, int]) -> dict | None:
    should = case.get("should") or []
    prompt = recall_synth.render(
        "verifier_A6", prompts["verifier_A6"],
        question=case["query"], quotes=recall_synth.quotes_block(should),
        **recall_synth.intent_slots(prompts["intent_factual_A3"]))
    return _ask_once(llm, prompt, usage)


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
                evidence_ids = [f"e{i}" for i in range(1, len(case["should"]) + 1)]
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


def run_anchors(books: list[Book], out_dir: str | Path, k: int = 30) -> dict:
    """每本（只给没有人工题的行）抽 ``k`` 个锚点，写 ``<stem>.anchors.json``。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, int] = {}
    for book in books:
        if book.cases:
            continue
        anchors = recall_synth.sample_anchors(
            book.source, [book.character, *book.aliases], k=k)
        payload = {
            "book": book.book,
            "title": book.title,
            "text_id": book.text_id,
            "character": book.character,
            "aliases": book.aliases,
            "anchors": [{"id": f"a{i:02d}", **a} for i, a in enumerate(anchors, 1)],
        }
        _write_json(out / f"{_stem(book)}.anchors.json", payload)
        summary[_stem(book)] = len(anchors)
    return summary


def _read_anchors(path: Path, book: Book) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalAbort(f"{_stem(book)} 还没有锚点文件：{path}（先跑 anchors）") from None
    return payload["anchors"]


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

    evidence_ids = [f"e{i}" for i in range(1, len(should) + 1)]
    verifier_prompt = recall_synth.render(
        "verifier_A6", prompts["verifier_A6"], question=question, quotes=quotes,
        **recall_synth.intent_slots(prompts["intent_factual_A3"]))
    verdict = _ask_gate(
        llm, verifier_prompt,
        lambda value: recall_synth.verify_ok(value, evidence_ids), usage)
    if verdict is None:
        return _parse_discard(draft, attempt), raws
    raws["verdict"] = verdict

    return recall_synth.judge(
        draft, anchor, source_norm, critic=critic,
        verdict=verdict, attempt=attempt), raws


def run_check(
    books: list[Book], llm: Any, prompts: dict[str, str],
    drafts_dir: str | Path, out_dir: str | Path, *, round_no: int = 1,
) -> dict:
    """整批核查：先台账（有问题整批拒收），再逐条过关，写三份产物。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    drafts_dir = Path(drafts_dir)

    prepared: list[tuple[Book, dict[str, dict], list[dict], list[str]]] = []
    problems: list[str] = []
    for book in books:
        if book.cases:
            continue
        stem = _stem(book)
        anchors = _read_anchors(out / f"{stem}.anchors.json", book)
        drafts_path = drafts_dir / f"{stem}.jsonl"
        drafts = _read_jsonl(drafts_path) if drafts_path.exists() else []
        discarded = _load_str_list(out / f"{stem}.discarded.json", [])
        problems.extend(recall_synth.ledger_problems(
            [d.get("id") for d in drafts],
            [a["id"] for a in anchors],
            discarded,
        ))
        prepared.append((book, {a["id"]: a for a in anchors}, drafts, discarded))
    if problems:
        raise EvalAbort("台账未通过，整批拒收：\n" + "\n".join(problems))

    usage = _new_usage()
    verdict_rows: list[dict] = []
    repair_rows: list[dict] = []
    summary: dict[str, Any] = {}

    for book, anchors, drafts, discarded in prepared:
        stem = _stem(book)
        source_norm = normalize(book.source)
        passed_rows: list[dict] = []
        newly_discarded: list[str] = []
        book_results: list[dict] = []
        skipped = 0

        for draft in drafts:
            if draft.get("skip"):
                skipped += 1
                continue
            result, raws = _run_gates(
                book, draft, anchors[draft["id"]], source_norm, llm, prompts,
                usage, round_no)
            book_results.append(result)
            verdict_rows.append({"id": draft["id"], "round": round_no, **result, "raw": raws})
            if result["status"] == "passed":
                passed_rows.append({
                    "id": draft["id"],
                    "phase": None,
                    "query": draft["query"],
                    "should": draft["should"],
                    "should_not": draft.get("should_not") or [],
                    "own_words_flag": result["own_words_flag"],
                })
            elif result["status"] == "discarded":
                newly_discarded.append(draft["id"])
            else:
                repair_rows.append({
                    "id": draft["id"], "round": round_no,
                    "status": result["status"], "problems": result["problems"],
                })

        _write_jsonl(out / f"{stem}.jsonl", passed_rows)
        if newly_discarded:
            _write_json(
                out / f"{stem}.discarded.json",
                sorted(set(discarded) | set(newly_discarded)))
        summary[stem] = {
            "anchors": len(anchors),
            "drafts": len(drafts),
            "skip": skipped,
            "passed": len(passed_rows),
            "flagged": sum(1 for row in passed_rows if row["own_words_flag"]),
            "outcomes": recall_synth.outcome_counts(book_results),
            "overlap": recall_synth.overlap_histogram(
                recall_synth.lexical_overlap(row["query"], row["should"])
                for row in passed_rows),
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

    _write_jsonl(out / "verdicts.jsonl", verdict_rows)
    _write_jsonl(out / "repair.jsonl", repair_rows)
    return {"round": round_no, "books": summary, "usage": usage}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="0d 出题工具（probe / anchors / check）")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="人工题过指定模型关各两次（不改题）")
    probe.add_argument("--suite", required=True)
    probe.add_argument("--prompts", required=True)
    probe.add_argument("--out", required=True)
    probe.add_argument("--gates", nargs="+", choices=PROBE_GATES, default=list(PROBE_GATES))

    anchors = sub.add_parser("anchors", help="按 60 段抽锚点")
    anchors.add_argument("--suite", required=True)
    anchors.add_argument("--out-dir", dest="out_dir", required=True)
    anchors.add_argument("--k", type=int, default=30)

    check = sub.add_parser("check", help="台账 + 四道关，写 verdicts / repair / 题")
    check.add_argument("--suite", required=True)
    check.add_argument("--prompts", required=True)
    check.add_argument("--drafts", required=True)
    check.add_argument("--out-dir", dest="out_dir", required=True)
    check.add_argument("--round", type=int, default=1)
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> None:
    if args.command == "check" and not 1 <= args.round <= recall_synth.MAX_ATTEMPTS:
        raise EvalAbort(f"--round 只接受 1..{recall_synth.MAX_ATTEMPTS}")

    from web.deps import get_storage, get_user_llm

    entries = load_suite(args.suite)
    storage = get_storage()
    try:
        with system_llm_context():
            books = [await load_book(storage, entry) for entry in entries]
            if args.command == "anchors":
                summary = run_anchors(books, args.out_dir, k=args.k)
            else:
                prompts = _load_prompts(args.prompts)
                user_ids = sorted({book.user_id for book in books})
                if len(user_ids) != 1:
                    raise EvalAbort(f"书目跨多个用户：{user_ids}（本次只取一个用户的 key）")
                llm = await get_user_llm(user_ids[0], storage)
                if llm is None:
                    raise EvalAbort("该用户没有自己的 LLM key（三道模型关只走用户自己的 key）")
                if args.command == "probe":
                    summary = run_probe(books, llm, prompts, args.out, tuple(args.gates))
                else:
                    summary = run_check(
                        books, llm, prompts, args.drafts, args.out_dir,
                        round_no=args.round)
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
