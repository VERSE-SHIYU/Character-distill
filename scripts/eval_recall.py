#!/usr/bin/env python3
"""召回评测（段 0a 工具；0a 不跑真数据）。

读一次卡片与原文 → 用**聊天同一次检索**（``core.scene_retrieval.scene_hits``）
取前 3 段 → 交给 ``core.recall_eval`` 算 R@3 / 越界率 → 走 ``write_evidence``。

只读：不写库、不建会话、不调大模型。每条查询只花一次查询嵌入。

用法::

    python scripts/eval_recall.py --user-id <id> --card-id <id> \
        --cases tests/eval/recall/<名字>.jsonl --evidence-id <id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import recall_eval, scene_retrieval  # noqa: E402
from core.arc_view import arc_view  # noqa: E402
from core.quotes import normalize  # noqa: E402
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


def run_eval(source: str, card: CharacterCard, cases: list[dict], rag) -> dict:
    """先自检标注，再逐条检索；任何检索前失败都不花一次嵌入调用。"""
    source_norm = normalize(source)
    views = {case["phase"]: arc_view(card, case["phase"]) for case in cases}
    recall_eval.validate_cases(
        cases, source_norm, {phase: view.before for phase, view in views.items()})

    rows = []
    for case in cases:
        hits = scene_retrieval.scene_hits(
            rag, case["query"],
            character_name=card.name,
            window=views[case["phase"]].window,
        )
        rows.append(recall_eval.score_case(source_norm, case, hits))
    return recall_eval.aggregate(rows)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="召回评测（只读；不写库、不调大模型）")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--card-id", required=True)
    parser.add_argument("--cases", required=True, help="标注 JSONL 文件")
    parser.add_argument("--evidence-id", required=True, help="证据产物 id（须先登记）")
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(PROJECT_ROOT / "tests" / "perf"))
    from evidence_writer import code_sha, write_evidence
    from web.deps import get_indexing_service, get_storage

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
        write_evidence(
            args.evidence_id, result,
            claim=(f"{loaded.card.name} R@3 {result['r_at_3']:.2f} / "
                   f"越界率 {result['leak_rate']:.2f}（n={result['n']}，"
                   f"基线 tag recall-baseline-2026-10）"),
            script="scripts/eval_recall.py",
            env="用户自己的百炼 embedding key；检索 = 聊天同一次 scene_hits（top_k=3）",
            code_sha=code_sha(),
        )
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
