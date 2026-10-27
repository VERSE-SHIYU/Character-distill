#!/usr/bin/env python3
"""召回评测（段 0a 工具；0a 不跑真数据）。

读一次卡片与原文 → 用**聊天同一次检索**（``core.scene_retrieval.scene_hits``）
取前 3 段 → 交给 ``core.recall_eval`` 算 R@3 / 越界率 → 走 ``write_evidence``。

只读：不写库、不建会话、不调大模型。每条查询只花一次查询嵌入。

评测与聊天同一时限：每条查询现开 ``embed_deadline(now + SCENE_TOOL_BUDGET_S)``，
与聊天里 ``AgentToolkit.execute`` 给场景工具的预算是同一个数（``core.agent.tools``
唯一出处）；查询嵌入与重排共用它，超出即按聊天同样退回向量顺序（``rerank_fallback``）。

算与写分开：

- 检索模式（服务器上跑）：把结果 JSON 打到 stdout；不写证据。
- ``--from-json``（本地跑）：读已产出的结果，只调 ``write_evidence``。

用法::

    python scripts/eval_recall.py --user-id <id> --card-id <id> \
        --cases tests/eval/recall/<名字>.jsonl

    python scripts/eval_recall.py --from-json result.json --evidence-id <id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import recall_eval, scene_retrieval  # noqa: E402
from core.agent.tools import SCENE_TOOL_BUDGET_S  # noqa: E402
from core.arc_view import arc_view  # noqa: E402
from core.embeddings import embed_deadline  # noqa: E402
from core.quotes import normalize  # noqa: E402
from core.scene_indexer import ChunkScheme  # noqa: E402
from core.schema import CharacterCard  # noqa: E402


class EvalAbort(RuntimeError):
    """取数前置条件不满足 —— 在检索前退出。"""


@dataclass
class Loaded:
    card: CharacterCard
    text_id: str
    source: str
    embedding_key: str
    embedding_region: str


@dataclass
class Book:
    """一本书里的一个角色 —— `suite.json` 的一行（⑭ 的评测单位）。"""
    book: str
    title: str
    user_id: str
    text_id: str
    character: str
    source: str
    aliases: list[str]
    cases: str | None
    card_id: str | None
    card: CharacterCard | None
    embedding_key: str
    embedding_region: str


def load_cases(path: str | Path) -> list[dict]:
    cases = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if not cases:
        raise EvalAbort(f"标注文件为空：{path}")
    return cases


async def load_inputs(storage, *, user_id: str, card_id: str) -> Loaded:
    """只在缺凭据/缺数据时抛 ``EvalAbort``；没有 key 时提示语与生产同源。"""
    from core.embeddings import EMBEDDING_KEY_REQUIRED
    from web.llm_resolution import resolve_embedding

    card_rec = await storage.get_card_owned(card_id, user_id)
    if not card_rec:
        raise EvalAbort(f"卡片不存在或不属于该用户：{card_id}")
    text_id = card_rec.get("text_id") or ""
    if not text_id:
        raise EvalAbort(f"卡片没有原文，无法评测召回：{card_id}")
    text_rec = await storage.get_text_owned(text_id, user_id)
    if not text_rec:
        raise EvalAbort(f"原文不存在或不属于该用户：{text_id}")

    emb = resolve_embedding(await storage.get_user_api_config(user_id) or {})
    if not emb.key:
        raise EvalAbort(EMBEDDING_KEY_REQUIRED)
    return Loaded(
        card=CharacterCard.model_validate_json(card_rec["card_json"]),
        text_id=text_id,
        source=text_rec.get("content", ""),
        embedding_key=emb.key,
        embedding_region=emb.region,
    )


def load_suite(path: str | Path) -> list[dict]:
    """读书目（`suite.json`）：每行一本书里的一个角色；`cases` 相对 suite 文件解析。"""
    suite_path = Path(path)
    try:
        entries = json.loads(suite_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalAbort(f"读不了书目 {suite_path}：{exc}") from exc
    if not isinstance(entries, list) or not entries:
        raise EvalAbort(f"书目为空或不是数组：{suite_path}")
    base = suite_path.parent
    for entry in entries:
        cases = entry.get("cases")
        if cases:
            entry["cases"] = str((base / cases).resolve())
    return entries


async def load_book(storage, entry: dict) -> Book:
    """按 suite 的一行读书：原文、主角名与别名、可选卡、用户自己的嵌入 key。

    名单为空、主角不在名单里、缺原文 / 卡 / key 都在**检索前**抛 ``EvalAbort``：
    这三样缺一个，跑下去的只是空结果（F6 / F7 / F15）。
    """
    from core.character_roster import aliases_for, cached_characters
    from core.embeddings import EMBEDDING_KEY_REQUIRED
    from web.llm_resolution import resolve_embedding

    title = entry.get("title") or entry.get("book") or entry.get("text_id")
    text_id = entry.get("text_id") or ""
    character = entry.get("character") or ""
    user_id = entry.get("user_id") or ""
    if not (text_id and character and user_id):
        raise EvalAbort(f"书目行缺 text_id / character / user_id：{title}")
    text_rec = await storage.get_text_owned(text_id, user_id)
    if not text_rec:
        raise EvalAbort(f"原文不存在或不属于该用户：{title}（{text_id}）")

    chars = await cached_characters(storage, text_id, user_id)
    if not chars:
        raise EvalAbort(f"{title} 的角色名单为空 —— 先在网站上对这本书点「识别角色」")
    if character not in {c.get("name") for c in chars}:
        raise EvalAbort(f"{title} 的名单里没有「{character}」")

    card_id = entry.get("card_id") or None
    card = None
    if card_id:
        card_rec = await storage.get_card_owned(card_id, user_id)
        if not card_rec:
            raise EvalAbort(f"卡片不存在或不属于该用户：{card_id}")
        card = CharacterCard.model_validate_json(card_rec["card_json"])

    emb = resolve_embedding(await storage.get_user_api_config(user_id) or {})
    if not emb.key:
        raise EvalAbort(EMBEDDING_KEY_REQUIRED)

    return Book(
        book=entry.get("book") or title,
        title=title,
        user_id=user_id,
        text_id=text_id,
        character=character,
        source=text_rec.get("content", ""),
        aliases=aliases_for(chars, character),
        cases=entry.get("cases") or None,
        card_id=card_id,
        card=card,
        embedding_key=emb.key,
        embedding_region=emb.region,
    )


def _scene_hits_in_chat_budget(rag, query: str, *, character_name, window):
    """聊天同一次检索 + 聊天同一时限：deadline 每条查询现算，不跨查询累计。"""
    with embed_deadline(time.monotonic() + SCENE_TOOL_BUDGET_S):
        return scene_retrieval.scene_hits(
            rag, query, character_name=character_name, window=window)


def run_eval(source: str, card: CharacterCard, cases: list[dict], rag) -> dict:
    """先自检标注，再逐条检索；任何检索前失败都不花一次嵌入调用。"""
    source_norm = normalize(source)
    views = {case["phase"]: arc_view(card, case["phase"]) for case in cases}
    recall_eval.validate_cases(
        cases, source_norm, {phase: view.before for phase, view in views.items()})

    spans = rag.chunk_spans(len(source_norm))
    rows = []
    for case in cases:
        hits = _scene_hits_in_chat_budget(
            rag, case["query"],
            character_name=card.name,
            window=views[case["phase"]].window,
        )
        rows.append(recall_eval.score_case(source_norm, case, hits, spans))
    return recall_eval.aggregate(rows)


def run_eval_no_phase(source: str, character: str, cases: list[dict], rag) -> list[dict]:
    """没有卡的书：窗口恒 None，`phase` 必须为 null（`suite.json` 的生成题）。"""
    for case in cases:
        if case.get("phase") is not None:
            raise EvalAbort(f"没有卡的题 phase 必须为 null：{case.get('id')}")
    source_norm = normalize(source)
    recall_eval.validate_cases(cases, source_norm, {None: None})
    spans = rag.chunk_spans(len(source_norm))
    rows = []
    for case in cases:
        hits = _scene_hits_in_chat_budget(
            rag, case["query"], character_name=character, window=None)
        rows.append(recall_eval.score_case(source_norm, case, hits, spans))
    return rows


def eval_card_id(text_id: str, slot: str, scheme: ChunkScheme | None) -> str:
    """评测集合的 card_id（集合名 = `scenes_{card_id}`）—— **只此一处**生成。

    ``slot``：无卡行 `c{n}`，有卡行 `card`。不传切法沿用 0e 基线的 `eval_{text_id}_{slot}`；
    传了带切法前缀 `eval_r1_{mode}-{chars}-{overlap}_`（Chroma 集合名不能含冒号）。
    """
    if scheme is None:
        return f"eval_{text_id}_{slot}"
    return f"eval_r1_{scheme.mode}-{scheme.chars}-{scheme.overlap}_{text_id}_{slot}"


def run_suite(books: list[Book], service, scheme: ChunkScheme | None = None) -> dict:
    """一次跑完书目。无卡行建场景集合（n 只数无卡行）再走会话检索。

    不传 ``scheme``：有卡行照旧读线上卡集合（0e 基线的跑法）。传了：有卡行也按该切法建
    eval 集合（`…_card`）再检索，不读也不重建线上 `scenes_{card_id}`；窗口仍取自卡。
    """
    rows_by_book: dict[str, list[dict]] = {}
    per_text: dict[str, int] = {}
    for book in books:
        if not book.cases:
            raise EvalAbort(f"书目行没有标注文件：{book.title}/{book.character}")
        cases = load_cases(book.cases)
        if book.card is not None:
            card_id = book.card_id or ""
            if scheme is not None:
                card_id = eval_card_id(book.text_id, "card", scheme)
                service.build_eval_scene_index(
                    card_id, book.source, book.character,
                    book.embedding_key, book.embedding_region, scheme=scheme)
            rag = service.get_rag_for_session(
                book.text_id, card_id=card_id,
                embedding_key=book.embedding_key, embedding_region=book.embedding_region)
            if rag is None:
                raise EvalAbort(f"无法建立会话检索：{book.title}/{book.character}")
            rows = run_eval(book.source, book.card, cases, rag)["per_query"]
        else:
            # n 只数无卡行：有卡行不占编号。
            per_text[book.text_id] = per_text.get(book.text_id, 0) + 1
            card_id = eval_card_id(book.text_id, f"c{per_text[book.text_id]}", scheme)
            service.build_eval_scene_index(
                card_id, book.source, book.character,
                book.embedding_key, book.embedding_region, scheme=scheme)
            rag = service.get_rag_for_session(
                book.text_id, card_id=card_id,
                embedding_key=book.embedding_key, embedding_region=book.embedding_region)
            if rag is None:
                raise EvalAbort(f"无法建立会话检索：{book.title}/{book.character}")
            rows = run_eval_no_phase(book.source, book.character, cases, rag)
        rows_by_book[f"{book.title}/{book.character}"] = rows
    return recall_eval.aggregate_suite(rows_by_book)


_ENV = ("用户自己的百炼 embedding key；检索 = 聊天同一次 scene_hits（top_k=3）；"
        "基线 tag recall-baseline-2026-10")


def _claim(result: dict) -> str:
    # tag 里带年份数字、产物里无可绑定字段，故 tag 放 env（claim 的数字须全部有出处）。
    return (f"R@3 {result['r_at_3']:.2f} / 越界率 {result['leak_rate']:.2f}"
            f"（n={result['n']}，基线 tag 见 env）")


def write_from_json(path: str | Path, evidence_id: str) -> None:
    """本地：读服务器产出的结果，只写证据 —— 不连库、不检索。"""
    sys.path.insert(0, str(PROJECT_ROOT / "tests" / "perf"))
    from evidence_writer import code_sha, write_evidence

    result = json.loads(Path(path).read_text(encoding="utf-8"))
    write_evidence(
        evidence_id, result,
        claim=_claim(result),
        script="scripts/eval_recall.py",
        env=_ENV,
        reproduce=("SG 容器：cd /app && PYTHONPATH=/app python /tmp/eval_recall.py "
                   "--user-id 3996bd7f23a34311 --card-id d541a44f3679 "
                   "--cases /tmp/qizhuo.jsonl > /tmp/result.json；本地："
                   "python scripts/eval_recall.py --from-json /tmp/result.json "
                   "--evidence-id recall-baseline-qizhuo"),
        code_sha=code_sha(),
    )


def _chunk_scheme_arg(value: str) -> ChunkScheme:
    try:
        return ChunkScheme.from_key(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="召回评测（只读；不写库、不调大模型）")
    parser.add_argument("--user-id")
    parser.add_argument("--card-id")
    parser.add_argument("--cases", help="标注 JSONL 文件")
    parser.add_argument("--from-json", dest="from_json", help="已有结果 JSON（只写证据）")
    parser.add_argument("--evidence-id", help="证据产物 id（须先登记；只配 --from-json）")
    parser.add_argument("--suite", help="书目 JSON（每行一本书里的一个角色）")
    parser.add_argument("--chunk-scheme", dest="chunk_scheme", type=_chunk_scheme_arg,
                        help="只配 --suite：按切法 mode:chars:overlap 建 eval 变体集合")
    args = parser.parse_args(argv)
    if args.chunk_scheme and not args.suite:
        parser.error("--chunk-scheme 只用于 --suite")
    if args.suite:
        if args.from_json or args.evidence_id or args.user_id or args.card_id or args.cases:
            parser.error("--suite 不能与单卡参数 / --from-json / --evidence-id 同用")
        return args
    if args.from_json:
        if not args.evidence_id:
            parser.error("--from-json 需要 --evidence-id")
        return args
    if args.evidence_id:
        parser.error("--evidence-id 只用于 --from-json：检索模式不写证据")
    missing = [name for name in ("user_id", "card_id", "cases") if not getattr(args, name)]
    if missing:
        parser.error("检索模式缺少 " + ", ".join(f"--{n.replace('_', '-')}" for n in missing))
    return args


async def _main(args: argparse.Namespace) -> None:
    if args.from_json:
        write_from_json(args.from_json, args.evidence_id)
        return

    from web.deps import get_indexing_service, get_storage

    if args.suite:
        storage = get_storage()
        try:
            books = [await load_book(storage, entry) for entry in load_suite(args.suite)]
            result = run_suite(books, get_indexing_service(), scheme=args.chunk_scheme)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            await storage.close()
        return


    storage = get_storage()
    try:
        loaded = await load_inputs(storage, user_id=args.user_id, card_id=args.card_id)
        cases = load_cases(args.cases)
        rag = get_indexing_service().get_rag_for_session(
            loaded.text_id,
            card_id=args.card_id,
            embedding_key=loaded.embedding_key,
            embedding_region=loaded.embedding_region,
        )
        if rag is None:
            raise EvalAbort("无法建立会话检索（原文为空或没有 embedding key）")

        result = run_eval(loaded.source, loaded.card, cases, rag)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await storage.close()


def main(argv: list[str] | None = None) -> None:
    try:
        asyncio.run(_main(_parse_args(argv)))
    except EvalAbort as exc:
        print(f"[eval_recall] {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
