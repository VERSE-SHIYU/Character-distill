# -*- coding: utf-8 -*-
"""tools.py 执行器泄漏探针（阶段 D before 基线）——单真实请求，阻塞 search_memory。

机制：
  decision 轮(mock 恒成功) → tool_call search_memory → tools.execute 起 1 线程 executor，
  handler 内 mem0 search 的 query embed 打 mock /v1/embeddings；设 embed_hang_ms=20000 →
  app 侧 OpenAI client(读超时 8s×3 次尝试) 阻塞 ~26s > MEMORY_TIMEOUT=5s →
  fut.result(5s) 先超时，execute 返回「工具执行超时」，shutdown(cancel_futures) 杀不掉
  已启动的 handler 线程 → 该线程挂到 embed 放弃(~26s)才回收。

  tool_query 给 NOVEL 文本 → app 侧按文本 keyed 的共享 embed LRU 必 miss → 真发 HTTP。
  先 warm 一次会话（冷启/卡片 embed 全部缓存）→ 探针请求只有 search query 一条 embed 挂起。

测量：
  全程 0.4s 采 /proc/1/task 线程数；记录请求完成时刻；断言完成后线程仍高（泄漏），
  到 embed 放弃(~26s)才回落。输出：baseline、峰值增量、回落耗时。

产物：写入出口落 `docs/evidence/<PROBE_EVIDENCE_ID>.json`（本档恒为 `tools-leak-window`）。
本探针需要 rig（app 容器 + mock + PG，见 `README.md`），不是本机裸跑可复现的。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "perf"))
import step4_load as L  # noqa: E402
import e2e_otel as E  # noqa: E402
from evidence_writer import code_sha, write_evidence  # noqa: E402

APP = "http://127.0.0.1:7862"
MOCK = "http://127.0.0.1:60950"

EVIDENCE_ID = os.environ.get("PROBE_EVIDENCE_ID", "")
if not EVIDENCE_ID:
    raise SystemExit("必须指定 PROBE_EVIDENCE_ID=tools-leak-window —— 落点与清单条目由它决定。")
ENV = ("rig（tests/perf/README.md 的拓扑）：app 容器 7862 + mock 60950 + PG 5435，"
       "mock preset=fast、MOCK_TOOL=search_memory，embed 挂起由 mock `embed_hang_ms` 注入")


def mock_post(**d) -> dict:
    req = urllib.request.Request(MOCK + "/admin/set", method="POST",
                                 data=json.dumps(d).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def threads_now() -> int | None:
    try:
        thr = subprocess.run(["docker", "exec", "cdload-app-1", "sh", "-c", "ls /proc/1/task | wc -l"],
                             capture_output=True, text=True, timeout=8)
        return int(thr.stdout.strip()) if thr.returncode == 0 else None
    except Exception:
        return None


def chat_once(token: str, sid: str, sock_t: float = 90.0) -> dict:
    payload = {
        "session_id": sid,
        "message": f"我是{sid}的用户。你记得我们之前一起经历过的事吗？挑一件你印象最深的讲讲。",
        "stream": True, "agent_mode": True,
    }
    return L._chat_once(token, payload, L.next_ip(), ttft_ms=15000, sock_t=sock_t)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hang-ms", type=int, default=20000, help="mock embed 挂起时长")
    ap.add_argument("--prefix", default="probe")
    ap.add_argument("--samples", type=float, default=1, help="并发探针请求数")
    ap.add_argument("--interval", type=float, default=0.4, help="线程采样间隔 s")
    args = ap.parse_args()

    n = int(args.samples)
    L.cmd_provision(SimpleNamespace(prefix=args.prefix, count=n))
    sids = [f"{args.prefix}{i:04d}" for i in range(n)]
    token = E._login(APP)

    # warm：每个会话一次正常 chat（卡片/冷启 embed 全部缓存），结果丢弃
    for sid in sids:
        r = chat_once(token, sid)
        print(f"[warm] {sid} cls={r['cls']} ttft={r['ttft_ms']}ms", flush=True)
    time.sleep(2)

    bs = [threads_now() for _ in range(3)]
    base = max(v for v in bs if v is not None)
    print(f"[probe] baseline threads = {bs} -> {base}", flush=True)

    q = f"leakprobe-{uuid.uuid4().hex[:12]}"
    cfg = mock_post(decision_fail_rate=0, embed_hang_ms=args.hang_ms, tool_query=q)
    print(f"[probe] mock set embed_hang_ms={args.hang_ms} tool_query={q[:18]}... "
          f"(cfg.embed_hang_ms={cfg['cfg']['embed_hang_ms']})", flush=True)

    rows: list[dict] = []
    stop = threading.Event()

    def samp() -> None:
        while not stop.is_set():
            rows.append({"t": time.monotonic(), "thr": threads_now()})
            stop.wait(args.interval)

    st = threading.Thread(target=samp, daemon=True)
    st.start()
    t0 = time.monotonic()

    results: list[dict] = []
    def work(i: int) -> None:
        results.append(chat_once(token, sids[i]))
    ws = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(n)]
    for w in ws:
        w.start()
    for w in ws:
        w.join()
    done_at = time.monotonic() - t0
    print(f"[probe] {n} request(s) done in {done_at:.1f}s: {[(r['cls'], r['ttft_ms']) for r in results]}", flush=True)

    # 观察窗固定到 t0+34s（覆盖 embed client ~26s 放弃 + 余量），不提前停；
    # 回落判定收紧为连续 ≥4 样本 == baseline。
    obs_end = t0 + 34.0
    settle_t: float | None = None
    while time.monotonic() < obs_end:
        time.sleep(0.3)
        thr = threads_now()
        rows.append({"t": time.monotonic(), "thr": thr})
        recent = [x["thr"] for x in rows[-4:] if x["thr"] is not None]
        if len(recent) >= 4 and all(v == base for v in recent):
            settle_t = time.monotonic() - t0
    stop.set()
    st.join(timeout=3)

    peak = max(x["thr"] for x in rows if x["thr"] is not None)
    print(f"[probe] peak threads = {peak} (baseline {base}, delta +{peak - base})", flush=True)
    # 泄漏窗：requests done 之后、且线程仍 > baseline 的持续时间
    over = [(x["t"] - t0) for x in rows if x["thr"] is not None and x["thr"] > base]
    over_after_done = [dt for dt in over if dt > done_at]
    print(f"[probe] requests done at t={done_at:.1f}s", flush=True)
    if over_after_done:
        print(f"[probe] threads > baseline after done for {over_after_done[-1] - done_at:.1f}s "
              f"(last elevated sample t={over_after_done[-1]:.1f}s)", flush=True)
    print(f"[probe] settle(=baseline x4) at t={settle_t:.1f}s" if settle_t else "[probe] no settle in window", flush=True)

    prev = None
    for x in rows:
        dt = x["t"] - t0
        cur = x["thr"]
        if prev is None or abs(cur - prev) > 0 or (4.0 <= dt <= 30.0 and int(dt * 2) % 4 == 0):
            mark = "   <== requests done" if abs(dt - done_at) < 0.35 else ""
            print(f"   t={dt:5.1f}s threads={cur}{mark}", flush=True)
            prev = cur

    leak_window = round(over_after_done[-1] - done_at, 1) if over_after_done else 0.0
    print("EVIDENCE " + write_evidence(
        EVIDENCE_ID,
        {"probe": "tools_leak", "embed_hang_ms": args.hang_ms,
         "baseline_threads": base, "peak_threads": peak, "thread_delta": peak - base,
         "requests_done_s": round(done_at, 1), "leak_window_s": leak_window,
         "settle_s": round(settle_t, 1) if settle_t else None, "samples": n},
        claim=(f"{n} 个真实请求 done 后线程仍 > 基线 {leak_window}s"
               f"（baseline {base} → peak {peak}，embed 挂 {args.hang_ms}ms）"),
        script="tests/perf/leak_probe.py", env=ENV, code_sha=code_sha(),
    ).as_posix(), flush=True)

    mock_post(decision_fail_rate=0, embed_hang_ms=0, tool_query="")
    print("[probe] mock reset embed_hang_ms=0 tool_query=''", flush=True)


if __name__ == "__main__":
    main()
