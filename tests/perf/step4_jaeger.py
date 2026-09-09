# -*- coding: utf-8 -*-
"""②④ Step 4 Jaeger 侧归并：从 chat.invoke_agent 根 span 读 app.ttft_ms / agent.degraded。

spec §1 的 TTFT P95 从 app.ttft_ms（服务端锚点，span 打开→首 token）取，客户端 ttft
（POST→首 token）作补充。压测时 OTLP Batch 有秒级延迟 → 每档次结束后拉一次本窗口。

用法：
  python tests/perf/step4_jaeger.py roots --lookback 30m          # 全量根统计
  python tests/perf/step4_jaeger.py bucket --dir <results> --glob 'fast_c*.json'
      # 按各档次 result 文件 sampler 时间窗，把根 span 归属各档次，出 p50/p95/degraded

取根：operationName == chat.invoke_agent（每请求一条 trace 一个根）。tags 读
app.ttft_ms、agent.degraded。agent.degraded=false 每个 agent 请求都会写（§7 埋点）——
存在性即验证 plumbing；Phase B 故障注入下应为 true。
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "perf"))
import e2e_otel as E  # noqa: E402  (复用 _req；Jaeger 端点同)

JAEGER = E.JAEGER_UI  # http://127.0.0.1:16686


def fetch_roots(lookback: str = "30m", limit: int = 2000) -> list[dict]:
    """拉 service=character-distill 全部 trace，抽 chat.invoke_agent 根 span 摘要。"""
    import urllib.parse
    q = urllib.parse.urlencode({"service": "character-distill",
                                "lookback": lookback, "limit": limit})
    code, body, _ = E._req("GET", f"{JAEGER}/api/traces?{q}", timeout=30)
    assert code == 200, f"jaeger {code}: {body[:200]!r}"
    roots = []
    for tr in json.loads(body).get("data", []):
        spans = tr.get("spans", [])
        for s in spans:
            if s.get("operationName") != "chat.invoke_agent":
                continue
            refs = s.get("references") or []
            if any(r.get("refType") == "CHILD_OF" for r in refs):
                continue  # 非根（理论上 invoke_agent 是根，防御）
            tags = {a["key"]: a.get("value") for a in s.get("tags", [])}
            roots.append({
                "start_us": s.get("startTime"),   # epoch 微秒
                "dur_ms": (s.get("duration") or 0) / 1000.0,
                "ttft": tags.get("app.ttft_ms"),
                "degraded": tags.get("agent.degraded"),
                "has_degraded": "agent.degraded" in tags,
                "trace": tr.get("traceID"),
            })
    return roots


def _bucket_windows(files: list[Path]) -> list[dict]:
    """从各 level result 的 sampler 时间窗推出 (start,end) epoch 秒。"""
    out = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        sam = d.get("sampler") or []
        if not sam:
            continue
        out.append({"file": f.name, "concurrency": d.get("concurrency"),
                    "start": sam[0]["t"] - 2.0, "end": sam[-1]["t"] + 2.0})
    out.sort(key=lambda x: x["start"])
    return out


def cmd_roots(args) -> None:
    roots = fetch_roots(args.lookback, args.limit)
    print(f"[jaeger] chat.invoke_agent roots in {args.lookback}: {len(roots)}")
    with_ttft = [r for r in roots if r["ttft"] is not None]
    with_dg = [r for r in roots if r["has_degraded"]]
    dg_true = sum(1 for r in with_dg if r["degraded"] is True)
    print(f"  with app.ttft_ms: {len(with_ttft)}")
    if with_ttft:
        vals = sorted(r["ttft"] for r in with_ttft)
        print(f"  ttft p50={vals[len(vals)//2]} p95={vals[min(len(vals)-1, int(.95*len(vals)))]} "
              f"min={vals[0]} max={vals[-1]}")
    print(f"  with agent.degraded attr: {len(with_dg)} (degraded=true: {dg_true})")


def cmd_bucket(args) -> None:
    files = sorted(Path(args.dir).glob(args.glob))
    if not files:
        print(f"no files match {args.dir}/{args.glob}"); return
    windows = _bucket_windows(files)
    if not windows:
        print("no sampler windows found"); return
    roots = fetch_roots(args.lookback, args.limit)
    # 分桶：根 start_us(µs)/1e6 → epoch 秒，落入哪个档次窗
    assigned: dict[int, list[dict]] = {i: [] for i in range(len(windows))}
    unmatched = 0
    for r in roots:
        t_s = (r["start_us"] or 0) / 1e6
        hit = False
        for i, w in enumerate(windows):
            if w["start"] <= t_s <= w["end"]:
                assigned[i].append(r); hit = True; break
        if not hit:
            unmatched += 1

    def p95(vals):
        if not vals:
            return None
        return sorted(vals)[min(len(vals) - 1, int(0.95 * len(vals)))]

    print(f"[jaeger] roots={len(roots)} unmatched_to_level={unmatched}")
    for i, w in enumerate(windows):
        rs = assigned[i]
        tts = [r["ttft"] for r in rs if r["ttft"] is not None]
        dgs = [r for r in rs if r["has_degraded"]]
        dg_true = sum(1 for r in dgs if r["degraded"] is True)
        print(f"  {w['file']:<16} C={w['concurrency']:<3} roots={len(rs):<4} "
              f"ttft_n={len(tts):<4} p50={p95(tts) and sorted(tts)[len(tts)//2]} "
              f"p95={p95(tts)} degraded_true={dg_true} has_attr={len(dgs)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("roots")
    pr.add_argument("--lookback", default="30m")
    pr.add_argument("--limit", type=int, default=2000)
    pb = sub.add_parser("bucket")
    pb.add_argument("--dir", default=str(ROOT / "data" / "eval_scratch" / "s4" / "results"))
    pb.add_argument("--glob", default="*.json")
    pb.add_argument("--lookback", default="40m")
    pb.add_argument("--limit", type=int, default=3000)
    args = ap.parse_args()
    if args.cmd == "roots":
        cmd_roots(args)
    else:
        cmd_bucket(args)


if __name__ == "__main__":
    main()
