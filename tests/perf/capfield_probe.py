"""追查 map_len_probe 里「out_tokens 顶到 8192 但 out_chars=0」的三条记录。

要回答：cap 打满时，究竟是
  (a) content 非空被截断（Tier 1 落地 → 可见失败，正是本次要建的闸）
  (b) content 为空、token 全花在别处（reasoning_content？）→ 落库空串，闸看不见的新失败形态
  (c) 两者都不是

结论：是 (b)。三条里两条 `content_chars=0` / `reasoning_content_chars=12441|12413` /
`finish_reason='length'` —— 思考与正文共享 max_tokens 预算，思考吃光预算。
原始产物由写入出口落 `docs/evidence/thinking-capfield.json`（id 由 `PROBE_EVIDENCE_ID` 指定）。

方法与 map_len_probe 同源：生产 _split_chunks 还原同一片、生产 map 提示词、生产参数
（temperature / presence_penalty）。唯一差别是本脚本**自己发 create()**（不走 async_chat），
以便 dump 原始响应对象——async_chat 只返回 message.content，会把「token 花在哪」这个信息丢掉。

注意：本脚本**故意**发修复前那套错方言 `extra_body={"enable_thinking": False}`（Qwen 方言，
DeepSeek 静默忽略）——它是「修复前」的可复现演示，不是待修的代码。生产侧的正确方言见
`adapters/llm_adapter.py` 的 `_THINKING_DISABLED`。

可替换项（换语料/换机器时改这三处）：
  - `PROBE_DB` 环境变量（默认 `data/character_sim.db`）
  - `PROBE_EVIDENCE_ID`：必填（本档恒为 `thinking-capfield`），决定落点与清单条目
  - `CASES` 里的 text_id（本机库行 id）与**角色名占位符**：换语料必须替换，
    未替换时脚本拒绝运行（见 `PLACEHOLDER_CHARS`）

版权：模型 output 按 map 规则会逐字保留原文对话 → 本脚本**不再落 `content_head` 字段**，
      产物只留长度与统计量，不留正文。
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
from adapters.llm_adapter import LLMAdapter           # noqa: E402
from core.distiller import Distiller                  # noqa: E402
from evidence_writer import code_sha, write_evidence  # noqa: E402

A._GEN_ATTEMPT_S = 120.0
A._GEN_DEADLINE_S = 240.0

DB = Path(os.environ.get("PROBE_DB", str(ROOT / "data" / "character_sim.db")))
EVIDENCE_ID = os.environ.get("PROBE_EVIDENCE_ID", "")
if not EVIDENCE_ID:
    raise SystemExit(
        "必须指定 PROBE_EVIDENCE_ID=thinking-capfield —— 产物落点与清单条目由它决定，"
        "没有默认值（留默认值就等于留一条绕过写入出口的路）。")
ENV = ("deepseek-v4-pro（单供应商），temperature=0.7；探针**故意**发修复前的错方言 "
       "extra_body={'enable_thinking': False}；语料 data/character_sim.db（真实语料，不入库）")
CAP = 8192
CONC = 3

# map_len_probe 里 out_chars == 0 的三条 (text_id, cs, idx, char, chunk_chars)
# text_id 是当时本机 data/character_sim.db 的行 id —— 不可读 hex，保留以便追溯；换库必须替换
# ⚠️ 角色名是**占位符**：入库版本刻意不写真实角色名（本仓是公开作品集，语料身份无证据价值）
CASES = [
    ("997207ccb4dd", 6000, 0, "角色B", 5996),
    ("997207ccb4dd", 6000, 6, "角色B", 5990),
    ("997207ccb4dd", 5000, 13, "角色B", 2117),
]

# 占位符集合：命中即拒绝运行（否则会拿占位名当角色名去拼 map 提示词，测出的是别的东西）
PLACEHOLDER_CHARS = {"角色A", "角色B", "角色C", "角色D"}


def chunk_of(text_id: str, cs: int, idx: int) -> str:
    c = sqlite3.connect(str(DB), timeout=15)
    try:
        content = c.execute("select content from texts where id=?", (text_id,)).fetchone()[0]
    finally:
        c.close()
    return Distiller._split_chunks(content, cs)[idx]


async def one(llm: LLMAdapter, client, case) -> dict:
    text_id, cs, idx, char, want_chars = case
    chunk = chunk_of(text_id, cs, idx)
    payload = llm._build_messages(
        Distiller._map_system_prompt(char),
        [{"role": "user", "content": Distiller._map_user_prompt(chunk, char)}],
    )
    t0 = time.monotonic()
    comp = await client.chat.completions.create(
        model=llm._model, messages=payload, temperature=llm._temperature,
        max_tokens=CAP, presence_penalty=llm._presence_penalty,
        timeout=120.0, extra_body={"enable_thinking": False},
    )
    el = round(time.monotonic() - t0, 1)
    choice = comp.choices[0]
    msg = choice.message
    content = msg.content or ""
    rc = getattr(msg, "reasoning_content", None)
    return {
        "text_id": text_id, "cs": cs, "idx": idx,
        "chunk_chars_expected": want_chars, "chunk_chars": len(chunk),
        "elapsed_s": el,
        "finish_reason": getattr(choice, "finish_reason", "<absent>"),
        "completion_tokens": (comp.usage.completion_tokens if comp.usage else None),
        "prompt_tokens": (comp.usage.prompt_tokens if comp.usage else None),
        "content_chars": len(content),
        "reasoning_content_chars": (len(rc) if isinstance(rc, str) else None),
        "msg_field_names": sorted(msg.model_dump().keys()) if hasattr(msg, "model_dump") else [],
    }


async def main() -> None:
    for _tid, _cs, _idx, _char, _ in CASES:
        if _char in PLACEHOLDER_CHARS:
            raise SystemExit(
                f"CASES 里的角色名 {_char!r} 是占位符 —— 替换为你自己语料里的角色名后再跑。"
                "（占位符会被当作角色名拼进 map 提示词，量出来的不是同一个东西）")
    llm = LLMAdapter()
    client = llm._make_async_client()
    sem = asyncio.Semaphore(CONC)

    async def guarded(c):
        async with sem:
            try:
                r = await one(llm, client, c)
            except Exception as exc:
                r = {"text_id": c[0], "cs": c[1], "idx": c[2],
                     "error": f"{type(exc).__name__}: {exc}"}
            print("[case] " + json.dumps(r, ensure_ascii=False), file=sys.stderr, flush=True)
            return r

    try:
        rows = await asyncio.gather(*[guarded(c) for c in CASES])
    finally:
        await client.close()

    empty = sum(1 for r in rows if r.get("content_chars") == 0)
    lengths = [r.get("finish_reason") for r in rows]
    claim = (f"顶到 {CAP} 上限时 {empty}/{len(rows)} 条 content_chars=0、"
             f"finish_reason={lengths} —— 思考（reasoning_content）与正文共享 max_tokens 预算")
    print("EVIDENCE " + write_evidence(
        EVIDENCE_ID, {"probe": "capfield", "model": llm._model, "cap": CAP, "records": rows},
        claim=claim, script="tests/perf/capfield_probe.py", env=ENV, code_sha=code_sha(),
    ).as_posix())


if __name__ == "__main__":
    asyncio.run(main())
