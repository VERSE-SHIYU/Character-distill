"""把录下来的一场一对一对话原样重放给本场记录的抽取（真模型，只跑抽取、不跑聊天），
按 spec `docs/specs/session-record-extract.md` §6 的判定线出结论。

用法（本地，需 DEEPSEEK_API_KEY）::

    python scripts/replay_session_record.py --transcript path/to/transcript.json \
        --card-name 孔乙己 --out e2e/scratch/replay-session-record

transcript.json 的格式就是上一场验收的产物：``{"turns": [{"turn", "kind", "user", "reply"}, ...]}``，
``kind`` ∈ seed / fill / change / question。抽取走的是生产同一个 `core.session_record.extract`
（同一份提示词、解析、校验），只把模型调用换成本脚本自己的适配器。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.llm_adapter import LLMAdapter
from core import session_record as rec
from core.tokens import count_tokens

# ── 判定线（spec §6；改这里要同时改 spec）──
FILL_KNOWN_MAX = 3            # 填充轮新增「知道的关于对方的事」不超过这么多条
BLOCK_TOKENS_MAX = 768        # 最后一轮后注入块不超过这么多 token（3 栏 × 256，BOOKMARKS 的每栏设置）
MONOLOGUE_MARKERS = ("不设防", "嘴上")   # 上一场独白腔条目的标记词（评估提示词 :253–256 来的）


def _replay(turns: list[dict], card_name: str, llm: LLMAdapter) -> tuple[rec.SessionRecord, list[dict]]:
    record, log = rec.SessionRecord(), []
    for t in turns:
        usage: dict = {}

        def call(system: str, user: str, usage: dict = usage) -> str:
            started = time.monotonic()
            reply = llm.chat(system, [{"role": "user", "content": user}])
            usage.update(llm.last_usage or {}, seconds=round(time.monotonic() - started, 2))
            return reply

        before = {e.id for e in record.entries}
        try:
            record, warns = rec.extract(record, char_name=card_name, user_message=t["user"],
                                        assistant_reply=t["reply"], call=call)
        except Exception as exc:  # noqa: BLE001 —— 记下来接着跑：一轮抽取失败本来就是「这一轮不更新」
            warns = [f"extract failed: {exc!r}"]
        new = [e for e in record.entries if e.id not in before]
        log.append({"turn": t["turn"], "kind": t["kind"], "warns": warns,
                    "new": [e.__dict__ for e in new], "usage": usage})
        print(f"[{t['turn']:>3} {t['kind']:<8}] +{len(new)} {[e.text for e in new]} {warns or ''}")
    return record, log


def _verdict(record: rec.SessionRecord, log: list[dict]) -> list[tuple[str, bool, str]]:
    def by_kind(kind: str) -> list[dict]:
        return [row for row in log if row["kind"] == kind]
    seeds_hit = sum(1 for row in by_kind("seed") if row["new"])
    changes = by_kind("change")
    superseded = [row for row in changes if any(e["replaces"] is not None for e in row["new"])]
    def duplicated(row: dict) -> bool:
        """改口那一轮：取代之外又新增了同一栏或同一句依据的条目（上一场 #14、#15 那种）。"""
        reps = [e for e in row["new"] if e["replaces"] is not None]
        return any(e["replaces"] is None and any(e["col"] == r["col"] or e["evidence"] == r["evidence"]
                                                 for r in reps) for e in row["new"])
    dup = [row["turn"] for row in changes if duplicated(row)]
    fill_known = sum(1 for row in by_kind("fill") for e in row["new"] if e["col"] == "known")
    monologue = [e.text for e in record.entries if any(m in e.text for m in MONOLOGUE_MARKERS)]
    block_tokens = count_tokens(rec.render_block(record))
    return [
        ("每条种子都记到", seeds_hit == len(by_kind("seed")), f"{seeds_hit}/{len(by_kind('seed'))}"),
        ("每处改口都用取代完成", len(superseded) == len(changes), f"{len(superseded)}/{len(changes)}"),
        ("改口那一轮没有重复记", not dup, f"重复的轮次 {dup}"),
        ("填充轮新增「了解」≤ 3", fill_known <= FILL_KNOWN_MAX, f"{fill_known} 条"),
        ("没有独白腔条目", not monologue, f"{monologue[:5]}"),
        ("注入块 ≤ 768 token", block_tokens <= BLOCK_TOKENS_MAX, f"{block_tokens} token"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--card-name", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    turns = json.loads(Path(args.transcript).read_text(encoding="utf-8"))["turns"]
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        print("需要 DEEPSEEK_API_KEY")
        return 2
    llm = LLMAdapter(api_key=key).derive(temperature=rec.EXTRACT_TEMPERATURE)

    record, log = _replay(turns, args.card_name, llm)
    checks = _verdict(record, log)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tokens_in = sum(r["usage"].get("prompt_tokens", 0) for r in log)
    tokens_out = sum(r["usage"].get("completion_tokens", 0) for r in log)
    secs = sorted(r["usage"].get("seconds", 0) for r in log)
    summary = {
        "checks": [{"name": n, "pass": ok, "detail": d} for n, ok, d in checks],
        "calls": len(log), "prompt_tokens": tokens_in, "completion_tokens": tokens_out,
        "seconds_mean": round(sum(secs) / max(len(secs), 1), 2),
        "seconds_p95": secs[int(len(secs) * 0.95) - 1] if secs else 0,
        "final_block": rec.render_block(record),
    }
    (out / "replay.json").write_text(json.dumps({"log": log, "record": record.to_persist()},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}：{d}" for n, ok, d in checks))
    print(f"calls={len(log)} in={tokens_in} out={tokens_out} "
          f"mean={summary['seconds_mean']}s p95={summary['seconds_p95']}s")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
