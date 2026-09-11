# -*- coding: utf-8 -*-
"""②④ Step 4 负载驱动：chat(agent_mode) 单档次并发压测 + 三采样点外部采样。

对齐 s4/step4_seed_pg.py 的 rig：容器 app 7862 / PG 5435 / 每档独立 mock 预设。

取数规则（spec §2）：
  - 丢弃每进程首样本（本驱动单进程 → warmup 请求跑一次即弃，不计入）；
  - chat 按请求逐条统计 TTFT（客户端全链路：POST → 首 token），流式不分段总时长；
  - 三采样点每 tick 同步采：app 线程数(docker exec /proc/1/task)、PG 连接 active/total、
    容器 RSS(docker stats)；
  - 失败按类分开计数：connect_refused / rate_limited / ttft_timeout / sse_break /
    http_error / error_event / no_done；ok 单独；
  - degraded 不在客户端可见（done 事件不带标记）——由 Jaeger app.ttft_ms + agent.degraded
    归并统计（见 pull_jaeger）。基线 mock 工具恒成功 → degraded 恒 false（构造性）。

限流规避：app 全路由按 X-Real-IP 限流（chat 30/min/IP、distill 16/hour/IP）。压测按
「并发用户」建模，每请求轮换海外 X-Real-IP（池 ≥ 128），使单 IP 速率远低于限流，同时
保持 geo guard 走海外分支。X-Real-IP 仅作流量整形，不回源。

用法：
  python tests/perf/step4_load.py provision 96        # 建 96 个 loadXXXX 会话行（幂等）
  python tests/perf/step4_load.py warmup              # 1 次丢弃请求（触发 pool/迁移）
  python tests/perf/step4_load.py level --preset fast --concurrency 8 --samples 100 \
      --ttft-ms 15000 --out data/eval_scratch/s4/results/fast_c08.json
并发模型：worker i 固定会话 load{i:04d}——每个并发用户一条独立会话。会话首请求即引擎
重建（RAG build），该样本丢弃（§2 首样本）。同会话锁不再串行不同 worker，测的是
系统级池/线程上限。结果 JSON：每请求 {ttft_ms, cls} + 汇总 + 三采样点时序。
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "perf"))

import e2e_otel as E  # noqa: E402  (login/_sse 帮手；app/PG/mock 指向点不同，见常量)

APP = "http://127.0.0.1:7862"
S4ENV = ROOT / "data" / "eval_scratch" / "s4" / ".env.s4"
RESULTS = ROOT / "data" / "eval_scratch" / "s4" / "results"
CHAT_SESSION = "75806c950ffc"   # 与 step4_seed_pg 拷贝的会话一致（seed 冒烟用）
USER_ID = "f46432a6a92e4ae7"    # testadmin id
CARD_ID = "fb975334594d"        # 与 step4_seed_pg 拷贝的卡片一致
SESSION_PREFIX = "load"         # 压测会话 id 前缀 load0000..load0095

# 海外源 IP 池（>500）：仅作 X-Real-IP 流量整形值（geo guard 海外分支 + 限流分桶）。
# 用 geoip 库能判为海外的知名段：Google DNS 8.8.0.0/16、Cloudflare 1.1.1.x、OpenDNS。
# 不回源，无路由语义。
IP_POOL = ([f"8.8.{h}.{i}" for h in range(0, 3) for i in range(1, 250)]
           + [f"1.1.1.{i}" for i in range(1, 250)]
           + [f"208.67.222.{i}" for i in range(1, 100)])
_IP_LOCK = threading.Lock()
_IP_IDX = 0


def next_ip() -> str:
    global _IP_IDX
    with _IP_LOCK:
        ip = IP_POOL[_IP_IDX % len(IP_POOL)]
        _IP_IDX += 1
        return ip


def _s4env() -> dict:
    d = {}
    for ln in S4ENV.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


# ── 单请求：chat agent_mode SSE + 分类 ──────────────────
def _chat_once(token: str, payload: dict, ip: str, ttft_ms: int, sock_t: float) -> dict:
    """跑一次 chat SSE。返回 {ttft_ms, cls, detail}。永不抛。"""
    req = urllib.request.Request(APP + "/api/chat/send", method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + token,
                                          **E.XREAL, "X-Real-IP": ip},
                                 data=json.dumps(payload, ensure_ascii=False).encode())
    t0 = time.monotonic()
    t_first: float | None = None
    buf = ""
    events: list[dict] = []
    saw_err = False
    done = False
    cls = "unknown"
    detail = ""
    try:
        with urllib.request.urlopen(req, timeout=sock_t) as r:
            while True:
                ch = r.read(4096)
                if not ch:
                    break
                buf += ch.decode("utf-8", "replace")
                while "\n\n" in buf:
                    raw, _, buf = buf.partition("\n\n")
                    for ln in raw.splitlines():
                        if not ln.startswith("data: "):
                            continue
                        try:
                            ev = json.loads(ln[6:])
                        except json.JSONDecodeError:
                            continue
                        if t_first is None and ("token" in ev or ev.get("done")):
                            t_first = time.monotonic()
                        if ev.get("done"):
                            done = True
                        if "error" in ev:
                            saw_err = True
                        events.append(ev)
    except urllib.error.HTTPError as exc:
        body = exc.read(200).decode("utf-8", "replace")
        if exc.code == 429:
            cls = "rate_limited"
        else:
            cls = f"http_{exc.code}"
        detail = body[:120]
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            cls = "connect_refused"
        else:
            cls = "net_error"
        detail = repr(exc.reason)[:120]
    except socket.timeout:
        cls = "ttft_timeout" if t_first is None else "sse_break"
        detail = f"first={'yes' if t_first else 'no'} timeout"
    except ConnectionError as exc:
        cls = "sse_break" if t_first is not None else "conn_abort"
        detail = repr(exc)[:120]
    except Exception as exc:
        cls = "sse_break" if t_first is not None else "exception"
        detail = repr(exc)[:120]

    if cls == "unknown":
        if saw_err:
            cls = "error_event"
        elif done:
            cls = "ok"
        else:
            cls = "no_done"
            detail = f"events={len(events)} last={events[-1] if events else None}"
    ttft = int((t_first - t0) * 1000) if t_first else None
    return {"ttft_ms": ttft, "cls": cls, "detail": detail, "events": len(events)}


# ── 采样器线程：线程数 + PG active/total + RSS ──────────
def sampler_loop(rows: list[dict], stop: threading.Event, interval: float = 1.5) -> None:
    """每 interval 秒采一个快照进 rows；docker exec 失败取 None。"""
    s4 = _s4env()
    dsn = f"postgresql://{s4['POSTGRES_USER']}:{s4['POSTGRES_PASSWORD']}@127.0.0.1:5435/{s4['POSTGRES_DB']}"
    import asyncio
    import asyncpg

    async def pg_snap() -> tuple[int, int] | None:
        try:
            c = await asyncpg.connect(dsn, timeout=3)
            try:
                row = await c.fetchrow(
                    "SELECT count(*) FILTER (WHERE state='active') AS active,"
                    " count(*) AS total FROM pg_stat_activity"
                    " WHERE datname=$1 AND pid <> pg_backend_pid()", s4["POSTGRES_DB"])
                return row["active"], row["total"]
            finally:
                await c.close()
        except Exception:
            return None

    loop = asyncio.new_event_loop()
    thr_loop = threading.Thread(target=loop.run_forever, daemon=True)
    thr_loop.start()
    try:
        while not stop.is_set():
            t = time.time()
            snap = {"t": round(t, 1)}
            try:
                thr = subprocess.run(
                    ["docker", "exec", "cdload-app-1", "sh", "-c", "ls /proc/1/task | wc -l"],
                    capture_output=True, text=True, timeout=8)
                snap["threads"] = int(thr.stdout.strip()) if thr.returncode == 0 else None
            except Exception:
                snap["threads"] = None
            fut = asyncio.run_coroutine_threadsafe(pg_snap(), loop)
            try:
                a, tot = fut.result(timeout=8)
                snap["pg_active"], snap["pg_total"] = a, tot
            except Exception:
                snap["pg_active"] = snap["pg_total"] = None
            try:
                st = subprocess.run(["docker", "stats", "cdload-app-1", "--no-stream", "--format",
                                     "{{.MemUsage}}"], capture_output=True, text=True, timeout=8)
                mem = st.stdout.strip()  # 形如 "179.8MiB / 768MiB"
                snap["mem"] = mem.split(" / ")[0] if mem else None
            except Exception:
                snap["mem"] = None
            rows.append(snap)
            stop.wait(interval)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thr_loop.join(timeout=3)


# ── provision：压测会话行 ───────────────────────────────
def _pg_connect():
    s4 = _s4env()
    import asyncpg
    dsn = f"postgresql://{s4['POSTGRES_USER']}:{s4['POSTGRES_PASSWORD']}@127.0.0.1:5435/{s4['POSTGRES_DB']}"
    return asyncpg.connect(dsn)


def cmd_provision(args) -> None:
    import asyncio
    prefix = args.prefix
    ids = [f"{prefix}{i:04d}" for i in range(args.count)]

    async def go():
        c = await _pg_connect()
        try:
            ins = 0
            for sid in ids:
                ex = await c.fetchval("SELECT 1 FROM sessions WHERE id=$1", sid)
                if ex:
                    continue
                await c.execute(
                    "INSERT INTO sessions (id, card_id, user_id) VALUES ($1, $2, $3)",
                    sid, CARD_ID, USER_ID)
                ins += 1
        finally:
            await c.close()
        return ins

    ins = asyncio.run(go())
    print(f"[provision] ensured {len(ids)} sessions {prefix}0000..{prefix}{args.count-1:04d} (inserted {ins})")


# ── level 编排 ─────────────────────────────────────────
def run_level(token: str, concurrency: int, samples: int, ttft_ms: int,
              sock_t: float, out: str, wall_s: int = 0, agent_mode: bool = True,
              session_prefix: str = SESSION_PREFIX) -> dict:
    """worker i 固定会话 {prefix}{i:04d}；每 worker 首个请求（引擎重建）丢弃不计样本。

    wall_s>0 时到点即停：保护饱和档次不至于无限重发挂死请求。
    agent_mode=False：不走 AgentLoop 工具路径（阶段 C executor 开销对比的非工具臂）。
    session_prefix 让每阶段用全新会话（phase A/B/C/D 各自 provision 前缀），避免历史增长混淆。
    """
    rows: list[dict] = []
    stop = threading.Event()
    sampler = threading.Thread(target=sampler_loop, args=(rows, stop), daemon=True)
    sampler.start()

    results: list[dict] = []
    state = {"recorded": 0, "t0": time.monotonic()}
    lock = threading.Lock()

    def _timeout() -> bool:
        return bool(wall_s) and (time.monotonic() - state["t0"]) > wall_s

    def worker(i: int):
        sid = f"{session_prefix}{i:04d}"
        # 消息内嵌会话号：同 canned 文本 + 确定性 mock 回复会让各会话 trajectories 相同，
        # 撞坏 mock 故障注入的 per-key 裁决（曾致 Phase B raise 档 100% degraded）。
        payload = {
            "session_id": sid,
            "message": f"我是{sid}的用户。你记得我们之前一起经历过的事吗？能不能讲讲你印象最深的那一段？",
            "stream": True, "agent_mode": agent_mode,
        }
        first = True
        while True:
            with lock:
                if state["recorded"] >= samples or _timeout():
                    return
            r = _chat_once(token, payload, next_ip(), ttft_ms, sock_t)
            if first:
                first = False  # 会话冷启动样本丢弃
                continue
            with lock:
                if state["recorded"] >= samples or _timeout():
                    return
                state["recorded"] += 1
                results.append(r)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True)
               for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    dur = time.monotonic() - state["t0"]
    sampler.join(timeout=5)

    from collections import Counter
    cls_count = Counter(r["cls"] for r in results)
    ttfts = sorted(r["ttft_ms"] for r in results if r["ttft_ms"] is not None)

    def pct(p: float) -> int | None:
        if not ttfts:
            return None
        idx = min(len(ttfts) - 1, int(p / 100.0 * len(ttfts)))
        return ttfts[idx]

    doc = {
        "concurrency": concurrency, "samples": samples, "recorded": len(results),
        "wall_s": wall_s, "wall_hit": len(results) < samples,
        "duration_s": round(dur, 1),
        "req_s": round(len(results) / dur, 2) if dur else None,
        "ttft_ms_threshold": ttft_ms,
        "cls_count": dict(cls_count),
        "ok": cls_count.get("ok", 0),
        "ttft_p50": pct(50), "ttft_p95": pct(95), "ttft_p99": pct(99),
        "ttft_n": len(ttfts),
        "ttft_over_threshold": sum(1 for v in ttfts if v is not None and v > ttft_ms),
        "agent_mode": agent_mode,
        "results": results,
        "sampler": rows,
    }
    if out:
        RESULTS.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def cmd_warmup(_args) -> None:
    """跑 1 次 chat 请求：触发 app PG pool 初始化 + 预热，结果丢弃（§2 首样本）。"""
    token = E._login(APP)
    r = _chat_once(token, {
        "session_id": CHAT_SESSION, "message": "warmup",
        "stream": True, "agent_mode": True,
    }, next_ip(), ttft_ms=8000, sock_t=180)
    print(f"[warmup] cls={r['cls']} ttft={r['ttft_ms']}ms")
    if r["cls"] != "ok":
        print("  detail:", r["detail"]); sys.exit(1)


def cmd_level(args) -> None:
    token = E._login(APP)
    doc = run_level(token, args.concurrency, args.samples, args.ttft_ms, args.sock_t, args.out,
                    wall_s=args.wall_s, agent_mode=not args.non_agent, session_prefix=args.prefix)
    c = doc["cls_count"]
    print(f"[level] C={doc['concurrency']} samples={doc['samples']} recorded={doc['recorded']} "
          f"dur={doc['duration_s']}s ({doc['req_s']}/s) ok={doc['ok']} cls={c} "
          f"wall_hit={doc['wall_hit']} agent_mode={doc['agent_mode']}")
    print(f"[level] ttft n={doc['ttft_n']} p50={doc['ttft_p50']} p95={doc['ttft_p95']} "
          f"p99={doc['ttft_p99']} over_thr={doc['ttft_over_threshold']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("provision")
    pr.add_argument("--count", type=int, default=96)
    pr.add_argument("--prefix", default=SESSION_PREFIX)
    sub.add_parser("warmup")
    p = sub.add_parser("level")
    p.add_argument("--preset", required=True, choices=["fast", "slow"])
    p.add_argument("--concurrency", type=int, required=True)
    p.add_argument("--samples", type=int, default=100)
    p.add_argument("--ttft-ms", type=int, default=15000, help="TTFT 超阈（阈值定死写报告）")
    p.add_argument("--sock-t", type=float, default=240, help="整体 socket 读超时")
    p.add_argument("--wall-s", type=int, default=300, help="档次墙钟上限（超时记录并停）")
    p.add_argument("--non-agent", action="store_true", help="不走 agent 工具路径（阶段 C 对照臂）")
    p.add_argument("--prefix", default=SESSION_PREFIX, help="会话前缀（每阶段各自 provision 干净会话）")
    p.add_argument("--out", default="")
    args = ap.parse_args()
    if args.cmd == "provision":
        cmd_provision(args)
    elif args.cmd == "warmup":
        cmd_warmup(args)
    else:
        cmd_level(args)


if __name__ == "__main__":
    main()
