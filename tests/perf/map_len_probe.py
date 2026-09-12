"""max_tokens 测量闸：真实语料 + 真实 LLM，跑生产 map 提示词，统计各片输出 token 数。

要回答的问题：`llm.max_tokens = 4096` 对 map 输出够不够？
  classic 档 effective_chunk_size = 6000 字符，map 规则 2 要求「原文对话原句必须完整保留」
  → 输出量接近输入量。若有一批片贴线/超线，Tier 1 落地会把静默截断变成可见失败。

产出数字（修复 thinking 方言前后各跑一批，n=14）见 `docs/evidence/thinking_budget_evidence.md`；
原始产物由写入出口落 `docs/evidence/thinking-maplen-after.json`（修复后）/
`thinking-maplen-before.json`（修复前）—— id 由 `PROBE_EVIDENCE_ID` 指定（无默认值）。

方法（不改生产代码）：
  - 语料：`data/character_sim.db` 真实 story 语料（本地无 classic 语料；classic 档与 story 档
    的 map 提示词完全相同，唯一差别是分片大小 → 按 6000 字符切片即为 classic 档的忠实测量）
  - 分片：生产 Distiller._split_chunks(content, 6000)，再按生产 relevant 过滤（含角色名）
  - 提示词：生产 Distiller._map_system_prompt / _map_user_prompt，一字不改
  - 调用：生产 LLMAdapter.async_chat(client=_make_async_client())，与 map 阶段同形
  - max_tokens=8192 **故意高于 4096** —— 测的是自然输出长度，不是被截断后的长度

可替换项（换语料/换机器时改这三处）：
  - `PROBE_DB` 环境变量（默认 `data/character_sim.db`）
  - `PROBE_EVIDENCE_ID`：必填，决定产物落点与清单条目（**落点不由本脚本选**，
    见 `docs/evidence/README.md`；修复前的 before 档复现要先回退方言，见清单 reproduce）
  - `PLAN` 里的 text_id（本机库行 id）与**角色名占位符**（`角色A`…`角色D`）：换语料必须替换，
    未替换时脚本拒绝运行（见 `PLACEHOLDER_CHARS`）

版权：模型输出按 map 规则会逐字保留原文对话 → 本脚本**不再落 `preview` 字段**，
      产物只留统计量与 id，不留正文。
"""
from __future__ import annotations

import asyncio
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
from evidence_writer import code_sha, write_evidence  # noqa: E402

# 仅本测量脚本内抬高预算：生产生成轮是 45s 单次上限 / 60s deadline，真实长输出会撞线
# 超时（首次运行 22/24 超时）。测的是「模型自然输出多长」，不是「45s 内能吐多少」。
# 这是 scratch 内的模块常量改写，生产源码零改动。
A._GEN_ATTEMPT_S = 120.0
A._GEN_DEADLINE_S = 240.0

DB = Path(os.environ.get("PROBE_DB", str(ROOT / "data" / "character_sim.db")))
EVIDENCE_ID = os.environ.get("PROBE_EVIDENCE_ID", "")
if not EVIDENCE_ID:
    raise SystemExit(
        "必须指定 PROBE_EVIDENCE_ID=thinking-maplen-after（或 -before）—— 产物落点与清单条目"
        "由它决定，没有默认值（留默认值就等于留一条绕过写入出口的路）。")
ENV = ("deepseek-v4-pro（单供应商），temperature=0.7，探针内把 _GEN_ATTEMPT_S/_GEN_DEADLINE_S "
       "抬到 120/240（生产 45/60）；语料 data/character_sim.db（真实语料，不入库）")
MEASURE_MAX_TOKENS = 8192     # 高于 4096：只在极少数情况下才会成为限制
PROD_CAP = 4096               # config.yaml llm.max_tokens
CONC = 3
NEAR_LINE = 3500              # 「贴线」判据：≥ cap 的 85%

# (text_id, tier_chunk_size, 采样片数, 角色名候选按出现次数取最大者)
# text_id 是当时本机 data/character_sim.db 的行 id —— 不可读 hex，保留以便追溯；换库必须替换
# ⚠️ 角色名是**占位符**：入库版本刻意不写真实角色名（本仓是公开作品集，语料身份无证据价值）。
#    换语料时替换为你自己文本里的角色名（可给多个候选，取出现次数最多者）。
PLAN = [
    ("0b353450811b", 6000, 3, ["角色A"]),
    ("997207ccb4dd", 6000, 3, ["角色B"]),
    ("921f19d057a3", 6000, 2, ["角色C"]),
    ("172239fd232b", 6000, 2, ["角色D"]),
    ("997207ccb4dd", 5000, 2, ["角色B"]),          # story 档对照
    ("921f19d057a3", 5000, 2, ["角色C"]),
]

# 占位符集合：命中即拒绝运行（否则 sample() 找不到含该串的片会静默退回前三片）
PLACEHOLDER_CHARS = {"角色A", "角色B", "角色C", "角色D"}


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
        if char in PLACEHOLDER_CHARS:
            raise SystemExit(
                f"PLAN 里的角色名 {char!r} 是占位符 —— 替换为你自己语料里的角色名后再跑。"
                "（占位符下 sample() 匹配不到任何片，会静默退回前三片，测的就不是同一个东西了）")
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
    payload = {
        "probe": "map_len",
        "model": llm.model,
        "measure_max_tokens": MEASURE_MAX_TOKENS,
        "prod_max_tokens": PROD_CAP,
        "records": items,
        "summary": [summarize([it for it in items if it["cs"] == cs], cs) for cs in tiers],
    }
    # claim 由本次实测现算，不写死：写死的 claim 与产物迟早对不上
    outs = sorted(it["out_tokens"] for it in items if it.get("out_tokens") is not None)
    n = len(outs)
    p50 = outs[min(n - 1, round(0.5 * (n - 1)))] if n else None
    claim = (f"map 自然输出 out_tokens p50 {p50}、max {outs[-1] if n else None}（n={n}）；"
             f"撞 8192 探针上限 {sum(1 for it in items if it.get('clipped_by_probe_cap'))}/{len(items)}、"
             f"空正文 {sum(1 for it in items if it.get('out_chars') == 0)}/{len(items)}")
    print("EVIDENCE " + write_evidence(
        EVIDENCE_ID, payload, claim=claim, script="tests/perf/map_len_probe.py",
        env=ENV, code_sha=code_sha(),
    ).as_posix())


if __name__ == "__main__":
    main()
