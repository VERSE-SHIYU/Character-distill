"""max_tokens 测量闸：真实语料 + 真实 LLM，跑生产 map 提示词，统计各片输出 token 数。

要回答的问题：`llm.max_tokens = 4096` 对 map 输出够不够？
  classic 档 effective_chunk_size = 6000 字符，map 规则 2 要求「原文对话原句必须完整保留」
  → 输出量接近输入量。若有一批片贴线/超线，Tier 1 落地会把静默截断变成可见失败。

产出数字（修复 thinking 方言前后各跑一批，n=14）见 `thinking_budget_evidence.md`；
原始产物 `out_maplen.prefix.json`（修复前）/ `out_maplen.json`（修复后）同目录入库。

方法（不改生产代码）：
  - 语料：`data/character_sim.db` 真实 story 语料（本地无 classic 语料；classic 档与 story 档
    的 map 提示词完全相同，唯一差别是分片大小 → 按 6000 字符切片即为 classic 档的忠实测量）
  - 分片：生产 Distiller._split_chunks(content, 6000)，再按生产 relevant 过滤（含角色名）
  - 提示词：生产 Distiller._map_system_prompt / _map_user_prompt，一字不改
  - 调用：生产 LLMAdapter.async_chat(client=_make_async_client())，与 map 阶段同形
  - max_tokens=8192 **故意高于 4096** —— 测的是自然输出长度，不是被截断后的长度

可替换项（换语料/换机器时改这三处）：
  - `PROBE_DB` 环境变量（默认 `data/character_sim.db`）
  - `PROBE_OUT_DIR` 环境变量（默认 `e2e/scratch/`，gitignored；**不会覆盖入库产物**）
  - `PLAN` 里的 text_id：取自当时本机库的行 id，换库必须替换

版权：模型输出按 map 规则会逐字保留原文对话 → 本脚本**不再落 `preview` 字段**，
      产物只留统计量与 id，不留正文。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import adapters.llm_adapter as A                      # noqa: E402
from adapters.llm_adapter import LLMAdapter          # noqa: E402
from core.distiller import Distiller                  # noqa: E402

# 仅本测量脚本内抬高预算：生产生成轮是 45s 单次上限 / 60s deadline，真实长输出会撞线
# 超时（首次运行 22/24 超时）。测的是「模型自然输出多长」，不是「45s 内能吐多少」。
# 这是 scratch 内的模块常量改写，生产源码零改动。
A._GEN_ATTEMPT_S = 120.0
A._GEN_DEADLINE_S = 240.0

DB = Path(os.environ.get("PROBE_DB", str(ROOT / "data" / "character_sim.db")))
OUT_DIR = Path(os.environ.get("PROBE_OUT_DIR", str(ROOT / "e2e" / "scratch")))
MEASURE_MAX_TOKENS = 8192     # 高于 4096：只在极少数情况下才会成为限制
PROD_CAP = 4096               # config.yaml llm.max_tokens
CONC = 3
NEAR_LINE = 3500              # 「贴线」判据：≥ cap 的 85%

# (text_id, tier_chunk_size, 采样片数, 角色名候选按出现次数取最大者)
# text_id 是当时本机 data/character_sim.db 的行 id —— 换语料必须替换
PLAN = [
    ("0b353450811b", 6000, 3, ["顾昀", "长庚", "沈易"]),
    ("997207ccb4dd", 6000, 3, ["汪东城", "吴庚霖"]),
    ("921f19d057a3", 6000, 2, ["汪东城", "炎亚纶"]),
    ("172239fd232b", 6000, 2, ["汪东城", "吴庚霖"]),
    ("997207ccb4dd", 5000, 2, ["汪东城", "吴庚霖"]),          # story 档对照
    ("921f19d057a3", 5000, 2, ["汪东城", "炎亚纶"]),
]


def db_one(sql, args=()):
    c = sqlite3.connect(str(DB), timeout=15)
    try:
        r = c.execute(sql, args).fetchone()
        return r[0] if r else None
    finally:
        c.close()


def pick_name(text_id: str, cands: list[str]) -> tuple[str, dict]:
    content = db_one("select content from texts where id=?", (text_id,)) or ""
    counts = {n: content.count(n) for n in cands}
    best = max(counts, key=lambda n: counts[n])
    return best, counts


def sample(text_id: str, char: str, cs: int, n: int):
    content = db_one("select content from texts where id=?", (text_id,)) or ""
    chunks = Distiller._split_chunks(content, cs)
    rel = [c for c in chunks if char in c] or chunks[:3]
    if not rel:
        return []
    if len(rel) <= n:
        idxs = list(range(len(rel)))
    else:
        idxs = sorted({round(i * (len(rel) - 1) / (n - 1)) for i in range(n)})
    return [(cs, i, rel[i]) for i in idxs]


async def run(llm: LLMAdapter, items: list[dict]) -> None:
    client = llm._make_async_client()      # 与生产 map 阶段同形（per-run client）
    sem = asyncio.Semaphore(CONC)

    async def one(it: dict) -> None:
        async with sem:
            system = Distiller._map_system_prompt(it["char"])
            user = Distiller._map_user_prompt(it["chunk"], it["char"])
            t0 = time.monotonic()
            try:
                text, usage = await llm.async_chat(
                    system, [{"role": "user", "content": user}],
                    max_tokens=MEASURE_MAX_TOKENS, client=client,
                )
                it["elapsed_s"] = round(time.monotonic() - t0, 1)
                it["in_tokens"] = (usage or {}).get("prompt_tokens")
                it["out_tokens"] = (usage or {}).get("completion_tokens")
                it["out_chars"] = len(text or "")
                it["clipped_by_probe_cap"] = bool(
                    it["out_tokens"] and it["out_tokens"] >= MEASURE_MAX_TOKENS - 5)
                print(f"[ok] {it['text_id']} cs={it['cs']} idx={it['idx']} "
                      f"chars={it['chunk_chars']} in={it['in_tokens']} out={it['out_tokens']} "
                      f"t={it['elapsed_s']}s", file=sys.stderr, flush=True)
            except Exception as exc:
                it["elapsed_s"] = round(time.monotonic() - t0, 1)
                it["error"] = f"{type(exc).__name__}: {exc}"
                print(f"[ERR] {it['text_id']} idx={it['idx']} t={it['elapsed_s']}s: {it['error']}",
                      file=sys.stderr, flush=True)

    try:
        await asyncio.gather(*[one(it) for it in items])
    finally:
        await client.close()


def summarize(items: list[dict], cs: int) -> dict:
    ok = [it for it in items if it.get("out_tokens") is not None]
    outs = sorted(it["out_tokens"] for it in ok)
    if not outs:
        return {"tier_chunk_size": cs, "n": 0}
    n = len(outs)
    def pct(p):
        return outs[min(n - 1, int(round(p / 100 * (n - 1))))]
    return {
        "tier_chunk_size": cs,
        "n": n,
        "errors": sum(1 for it in items if it.get("error")),
        "chunk_chars_min_med_max": [
            min(it["chunk_chars"] for it in ok),
            sorted(it["chunk_chars"] for it in ok)[n // 2],
            max(it["chunk_chars"] for it in ok),
        ],
        "out_tokens_min": outs[0],
        "out_tokens_p50": pct(50),
        "out_tokens_p90": pct(90),
        "out_tokens_max": outs[-1],
        "prod_cap": PROD_CAP,
        "ge_4096": sum(1 for v in outs if v >= PROD_CAP),
        "ge_near_line": sum(1 for v in outs if v >= NEAR_LINE),
        "near_line_threshold": NEAR_LINE,
        "any_clipped_by_probe_cap": any(it.get("clipped_by_probe_cap") for it in ok),
    }


def main() -> None:
    llm = LLMAdapter()      # 走 .env / config.yaml；不打印任何凭据
    items: list[dict] = []
    for text_id, cs, n, cands in PLAN:
        char, counts = pick_name(text_id, cands)
        picked = sample(text_id, char, cs, n)
        print(f"[plan] {text_id} cs={cs} char={char} counts={counts} picked={len(picked)}",
              file=sys.stderr, flush=True)
        for _cs, idx, chunk in picked:
            items.append({"text_id": text_id, "cs": _cs, "char": char, "idx": idx,
                          "chunk": chunk, "chunk_chars": len(chunk)})

    print(f"[plan] total calls = {len(items)}", file=sys.stderr, flush=True)
    asyncio.run(run(llm, items))

    for it in items:
        it.pop("chunk", None)     # 原始正文不进产物
    tiers = sorted({it["cs"] for it in items})
    result = {
        "probe": "map_len",
        "model": llm.model,
        "measure_max_tokens": MEASURE_MAX_TOKENS,
        "prod_max_tokens": PROD_CAP,
        "records": items,
        "summary": [summarize([it for it in items if it["cs"] == cs], cs) for cs in tiers],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "out_maplen.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT " + json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
