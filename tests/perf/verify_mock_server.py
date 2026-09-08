# -*- coding: utf-8 -*-
"""mock_llm_server.py 自验驱动：起子进程跑真实代码路径，实测并打印五项。

不联网（api_key 一律 mock 哑值），不触碰生产配置/数据。用法：
    python tests/perf/verify_mock_server.py
"""
from __future__ import annotations

import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from openai import OpenAI  # noqa: E402

MS = 1000.0
PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(preset: str) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = dict(os.environ, PRESET=preset)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "tests", "perf", "mock_llm_server.py"), "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
    )
    base = f"http://127.0.0.1:{port}/v1"
    for _ in range(100):  # 等就绪
        try:
            urllib.request.urlopen(base.rstrip("/v1") + "/health", timeout=0.5)
            return proc, base
        except Exception:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise SystemExit(f"mock server died: {out}")
            time.sleep(0.05)
    raise SystemExit("mock server not ready")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def make_adapter(base: str, max_tokens: int):
    from adapters.llm_adapter import LLMAdapter
    return LLMAdapter(api_key="mock", base_url=base, model="mock-chat",
                      temperature=0, max_tokens=max_tokens)


def stream_timing(base: str, max_tokens: int = 12) -> dict:
    """走真实 llm_adapter.chat_stream，记录每个 token 块到达时刻。"""
    llm = make_adapter(base, max_tokens)
    msgs = [{"role": "user", "content": "测一下流式节奏"}]
    arrivals, text = [], []
    t0 = time.monotonic()
    for piece in llm.chat_stream("mock-system", msgs):
        arrivals.append((time.monotonic() - t0) * MS)
        text.append(piece)
    return {"llm": llm, "arrivals": arrivals, "pieces": len(arrivals),
            "chars": sum(len(p) for p in text)}


def chat_engine_e2e(base: str) -> dict:
    from adapters.llm_adapter import LLMAdapter
    from core.chat_engine import ChatEngine
    from core.schema import CharacterCard
    llm = LLMAdapter(api_key="mock", base_url=base, model="mock-chat", temperature=0)
    card = CharacterCard(name="测试卡")
    engine = ChatEngine(llm=llm, rag=None, card=card, card_id="perf-verify")
    pieces = []
    t0 = time.monotonic()
    for piece in engine.chat_stream("今天过得怎么样？给我讲讲。", voice_mode=False):
        pieces.append(piece)
    return {"engine": engine, "pieces": pieces, "ms": (time.monotonic() - t0) * MS}


def embeddings_real(base: str, salt: str) -> dict:
    from core.embeddings import DashScopeEmbedding
    emb = DashScopeEmbedding(api_key="mock")
    emb._client = OpenAI(api_key="mock", base_url=base, timeout=8.0, max_retries=2)
    texts = [f"天气真好 {salt}", f"hello world {salt}"]  # salt 防跨 preset 撞共享缓存
    t0 = time.monotonic()
    v1 = emb._embed_impl(texts)
    t_net = (time.monotonic() - t0) * MS
    t0 = time.monotonic()
    emb._embed_impl(texts)  # 全命中共享缓存
    t_cache = (time.monotonic() - t0) * MS
    return {"v": v1, "dims": [len(x) for x in v1], "net_ms": t_net, "cache_ms": t_cache}


def bench_one(preset: str) -> dict:
    print(f"\n=== preset={preset} ===")
    proc, base = start_server(preset)
    try:
        st = stream_timing(base)
        arr = st["arrivals"]
        check(f"[{preset}] 流式按块逐条到达", st["pieces"] == 12, f"pieces={st['pieces']}")
        check(f"[{preset}] 首块延迟≈TTFT", len(arr) >= 2 and arr[0] > 0,
              f"first≈{arr[0]:.0f}ms arrivals={[round(a) for a in arr]}")
        if len(arr) >= 3:
            gaps = [b - a for a, b in zip(arr, arr[1:])]
            median_gap = statistics.median(gaps)
            check(f"[{preset}] 块间隔≈TOKEN_INTERVAL(非一次性吐完)", 0 < median_gap,
                  f"median_gap≈{median_gap:.0f}ms n={len(gaps)}")
        check(f"[{preset}] usage 已回填(last_usage)", st["llm"].last_usage is not None,
              json.dumps(st["llm"].last_usage) if st["llm"].last_usage else "")

        ee = chat_engine_e2e(base)
        full = "".join(ee["pieces"]).strip()
        check(f"[{preset}] chat_engine→llm_adapter 全程打 mock",
              full and ee["engine"].llm.last_usage is not None,
              f"e2e={ee['ms']:.0f}ms chars={len(full)} usage={ee['engine'].llm.last_usage}")

        em = embeddings_real(base, preset)
        check(f"[{preset}] embeddings dims==1024",
              all(d == 1024 for d in em["dims"]), f"dims={em['dims']}")
        check(f"[{preset}] 共享缓存命中≈0ms", em["cache_ms"] < 5,
              f"net={em['net_ms']:.0f}ms cache={em['cache_ms']:.0f}ms")
        return {
            "preset": preset, "first_ms": arr[0] if arr else None,
            "median_gap_ms": statistics.median(gaps) if len(arr) >= 3 else None,
            "stream_e2e_ms": ee["ms"], "embed_ms": em["net_ms"], "embed_cache_ms": em["cache_ms"],
        }
    finally:
        stop_server(proc)


def main() -> None:
    fast, slow = bench_one("fast"), bench_one("slow")
    assert fast and slow
    check("slow 比 fast 更慢(chat e2e)", slow["stream_e2e_ms"] > fast["stream_e2e_ms"],
          f"fast={fast['stream_e2e_ms']:.0f}ms slow={slow['stream_e2e_ms']:.0f}ms")
    check("slow 比 fast 更慢(embed)", slow["embed_ms"] > fast["embed_ms"],
          f"fast={fast['embed_ms']:.0f}ms slow={slow['embed_ms']:.0f}ms")

    print("\n=== 实测参照(压测基线) ===")
    print(f"{'preset':<6}{'first_token':>12}{'tok_interval':>12}{'e2e_chat':>12}{'embed':>12}")
    for r in (fast, slow):
        print(f"{r['preset']:<6}{r['first_ms']:>11.0f}ms{r['median_gap_ms']:>11.0f}ms"
              f"{r['stream_e2e_ms']:>11.0f}ms{r['embed_ms']:>11.0f}ms")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
