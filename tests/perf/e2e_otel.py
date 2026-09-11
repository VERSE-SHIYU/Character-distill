# -*- coding: utf-8 -*-
"""②④ Step 3/4 真实 server E2E 环境（可复用固化）。

起 Jaeger(docker) + mock_llm_server + 指向 scratch SQLite 的真实 uvicorn app，
把 testadmin 的 per-user LLM 配置 patch 到 mock；OTEL_ENABLED=1 + OTEL_EXPORTER=otlp
导到本机 Jaeger。驱动 chat(agent_mode) / distill 真实 HTTP 请求，产 span 树。

设计取舍：
- 只用 sqlite scratch 副本（data/eval_scratch/e2e/char_sim.db），绝不写生产库；
- 外部依赖全 mock：chat LLM / embed / mem0 LLM → mock 端点（EMBEDDING_BASE_URL、
  MEM0_LLM_BASE_URL 环境变量覆盖，DashScope/Mem0 本就支持）；DASHSCOPE/DEEPSEEK 用
  哑 key 占位（有 EMBEDDING_BASE_URL 覆盖就不会真打外网）；
- 分阶段：up / down / drive-chat / drive-distill / tree，环境在 up 后驻留，Step 4 直接复用。

用法：
  python tests/perf/e2e_otel.py up        # 起 mock + app，patch testadmin→mock
  python tests/perf/e2e_otel.py drive-chat      # agent_mode SSE 一轮
  python tests/perf/e2e_otel.py drive-distill   # 大文本 run_stream SSE + start 后台
  python tests/perf/e2e_otel.py tree      # 拉 Jaeger trace 树文本摘要
  python tests/perf/e2e_otel.py down      # 停 mock + app（Jaeger 保留）
状态（端口/PID/文本/会话）写 data/eval_scratch/e2e/*.json，跨命令复用。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRATCH = ROOT / "data" / "eval_scratch" / "e2e"
DB_SRC = ROOT / "data" / "character_sim.db"
DB = SCRATCH / "char_sim.db"
STATE = SCRATCH / "state.json"
JAEGER_UI = "http://127.0.0.1:16686"
OTLP = "http://127.0.0.1:4318"
TESTADMIN = "testadmin"
TESTADMIN_PWD = "test1234"          # 固定测试账号，禁动 Shiyu 账户
CHAT_SESSION = "75806c950ffc"       # card fb975334594d 上已有会话（scratch 副本验证过）
CHAT_TEXT_ID = "3d394865332c"
USER_ID = "f46432a6a92e4ae7"        # testadmin id
XREAL = {"X-Real-IP": "8.8.8.8"}    # 伪海外来源，绕过 domestic-LLM geo guard（本地压测专网）

sys.path.insert(0, str(ROOT))


# ── 小工具 ────────────────────────────────────────────
def _print(*a) -> None:
    print("[e2e]", *a, flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _req(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None,
         timeout: float = 60) -> tuple[int, bytes, dict]:
    h = dict(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _wait_http(url: str, tries: int = 200, delay: float = 0.25) -> bytes:
    for _ in range(tries):
        try:
            code, body, _ = _req("GET", url, timeout=1)
            if code < 500:
                return body
        except Exception:
            pass
        time.sleep(delay)
    raise TimeoutError(f"not ready: {url}")


# ── 环境状态读写 ──────────────────────────────────────
def _state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def _save(st: dict) -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _child_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    for k in ("OTEL_ENABLED", "OTEL_EXPORTER", "OTEL_EXPORTER_OTLP_ENDPOINT",
              "EMBEDDING_BASE_URL", "MEM0_LLM_BASE_URL"):
        env.pop(k, None)
    if extra:
        env.update(extra)
    return env


# ── up：mock + app ────────────────────────────────────
def _scratch_db() -> None:
    """从生产 dev db 做一份 scratch 副本（sqlite backup，不锁源）。"""
    import sqlite3
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    cs, cd = sqlite3.connect(str(DB_SRC)), sqlite3.connect(str(DB))
    with cs:
        cs.backup(cd)
    cd.close(); cs.close()
    _print(f"scratch db ready {DB} ({DB.stat().st_size} bytes)")


def _patch_llm(mock_base: str) -> None:
    """testadmin 的 user_secrets 指向 mock（走 storage 层，fernet 与 app 同源 .env）。"""
    import asyncio
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ["STORAGE_BACKEND"] = "sqlite"
    os.environ["DB_PATH"] = str(DB)
    from storage import get_store
    store = get_store()
    asyncio.run(store.update_user_api_config(
        USER_ID, api_key="sk-e2e-mock", base_url=f"{mock_base}/v1", model="deepseek-v4-pro"))
    _print(f"patched testadmin LLM -> {mock_base}/v1")


def cmd_up(_args) -> None:
    _scratch_db()

    mock_port = free_port()
    mock_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "perf" / "mock_llm_server.py"),
         "--port", str(mock_port), "--preset", "fast"],
        cwd=ROOT, env=_child_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    mock_base = f"http://127.0.0.1:{mock_port}"
    _wait_http(mock_base + "/health")
    _print(f"mock up {mock_base}/v1 (pid {mock_proc.pid})")

    _patch_llm(mock_base)

    app_port = free_port()
    app_env = _child_env({
        "STORAGE_BACKEND": "sqlite",
        "DB_PATH": str(DB),
        "OTEL_ENABLED": "1",
        "OTEL_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_ENDPOINT": OTLP,
        "EMBEDDING_BASE_URL": f"{mock_base}/v1",
        "MEM0_LLM_BASE_URL": f"{mock_base}/v1",
        "DASHSCOPE_API_KEY": "sk-e2e-dummy",
        "DEEPSEEK_API_KEY": "sk-e2e-dummy",
        "ALLOWED_ORIGINS": "http://localhost:5173",
    })
    app_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web.server:app",
         "--host", "127.0.0.1", "--port", str(app_port), "--log-level", "warning"],
        cwd=ROOT, env=app_env,
        stdout=open(SCRATCH / "app.log", "w", encoding="utf-8"),
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    _wait_http(f"http://127.0.0.1:{app_port}/api/health", tries=300, delay=0.2)
    _print(f"app up http://127.0.0.1:{app_port} (pid {app_proc.pid}) — OTEL on, log {SCRATCH/'app.log'}")

    _save({"mock_proc": mock_proc.pid, "app_proc": app_proc.pid,
           "mock_port": mock_port, "app_port": app_port,
           "mock_base": f"{mock_base}/v1", "app_base": f"http://127.0.0.1:{app_port}"})


def _popen_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_down(_args) -> None:
    st = _state()
    for key in ("app_proc", "mock_proc"):
        pid = st.get(key)
        if pid and _popen_alive(pid):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True) if os.name == "nt" else os.kill(pid, 9)
                _print(f"killed {key} pid {pid}")
            except Exception as exc:
                _print(f"kill {key} failed: {exc}")
    _save({})


# ── HTTP 驱动 ─────────────────────────────────────────
def _login(app_base: str) -> str:
    body = json.dumps({"username": TESTADMIN, "password": TESTADMIN_PWD}).encode()
    code, resp, _ = _req("POST", app_base + "/api/auth/login",
                         headers={"Content-Type": "application/json"}, body=body)
    assert code == 200, f"login {code}: {resp[:300]!r}"
    return json.loads(resp)["access_token"]


def _sse_events(url: str, token: str, payload: dict, timeout: float = 300) -> list[dict]:
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + token,
                                          **XREAL},
                                 data=json.dumps(payload, ensure_ascii=False).encode())
    events: list[dict] = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = ""
        while True:
            chunk = r.read(1024)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            while "\n\n" in buf:
                raw, _, buf = buf.partition("\n\n")
                for line in raw.splitlines():
                    if line.startswith("data: "):
                        try:
                            events.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass
    return events


def _multipart(fields: dict) -> tuple[bytes, str]:
    boundary = "----e2e" + str(time.time_ns())
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def cmd_drive_bg(_args) -> None:
    """§3.2 第二根：distill /start 后台线程根（distill.task）→ map 分片挂后台根。"""
    st = _state()
    app_base = st["app_base"]
    token = _login(app_base)
    text_id = st.get("distill_text_id")
    if not text_id:
        raise SystemExit("no distill_text_id in state — run drive-distill first")
    headers = {"Authorization": "Bearer " + token, **XREAL}
    body = json.dumps({"text_id": text_id, "character_name": "阿明", "force": False}).encode()
    code, resp, _ = _req("POST", app_base + "/api/distill/start",
                         headers={"Content-Type": "application/json", **headers}, body=body)
    assert code == 200, f"start {code}: {resp[:300]!r}"
    task_id = json.loads(resp)["task_id"]
    _print(f"bg distill task {task_id} started — polling")
    last = None
    for _ in range(120):  # ≤ ~40s
        time.sleep(0.5)
        c2, r2, _ = _req("GET", app_base + f"/api/distill/task/{task_id}", headers=headers)
        last = json.loads(r2)
        if last.get("status") in ("done", "error"):
            break
    _print(f"bg task final status={last.get('status')} msg={last.get('message', '')[:80]}")
    _print("sleep 6s for OTLP batch flush...")
    time.sleep(6)


def cmd_drive_chat(_args) -> None:
    st = _state()
    app_base = st["app_base"]
    token = _login(app_base)
    _print(f"login ok — chat session {CHAT_SESSION} agent_mode=True")
    events = _sse_events(app_base + "/api/chat/send", token, {
        "session_id": CHAT_SESSION, "message": "你记得我们之前聊过什么吗？我想听听你对我们过往的回忆。",
        "stream": True, "agent_mode": True,
    })
    done = [e for e in events if e.get("done")]
    _print(f"chat SSE events={len(events)} done={bool(done)} tokens={sum(1 for e in events if 'token' in e)}")
    st.setdefault("trace_roots", []).append("chat.invoke_agent")
    _save(st)
    _print("sleep 6s for OTLP batch flush...")
    time.sleep(6)


def _synthetic_prose(chars: int, name: str = "阿明") -> str:
    para = (f"{name}在灯下客栈的柜台后擦拭酒杯。"
            f"檐外雨声渐密，他想起前几日与阿芸的争执，一时怔住。"
            f"墙角的猫蹭了蹭他的靴子，他弯腰将它抱起，低声道：\"别闹，今夜有事。\"")
    n = max(1, chars // len(para) + 1)
    return (para * n)[:chars]


def cmd_drive_distill(_args) -> None:
    st = _state()
    app_base = st["app_base"]
    token = _login(app_base)
    # distill_incremental_stream 路由：低于 longctx_threshold(150k tok≈190k 字)走单调用
    # 长上下文流；≥ 阈值才进分块 Map(跨 ctx_thread async_chat) + Reduce。要看到 §3.2
    # 跨线程分片树，合成文本必须 ≥ ~200k 字。
    _print("upload synthetic ~320k prose to cross longctx threshold(150k tok) -> chunked map/reduce")
    content = _synthetic_prose(320_000)
    ct, ctype = _multipart({"text": content, "title": "e2e 多分片测试文本",
                            "filename": "e2e_multi.txt", "text_type": "story"})
    code, resp, _ = _req("POST", app_base + "/api/text/upload",
                         headers={"Authorization": "Bearer " + token, "Content-Type": ctype},
                         body=ct, timeout=120)
    assert code in (200, 201), f"upload {code}: {resp[:400]!r}"
    text_id = json.loads(resp).get("id") or json.loads(resp)["text_id"]
    st["distill_text_id"] = text_id
    st.setdefault("trace_roots", []).extend(["distill.stream", "distill.task"])
    _save(st)
    _print(f"uploaded text_id={text_id} — run_stream SSE (char_name=阿明, 跳过 auto-identify)")
    events = _sse_events(app_base + "/api/distill/run_stream", token,
                         {"text_id": text_id, "character_name": "阿明", "force": False})
    statuses = [e.get("status") for e in events if "status" in e]
    done = any(e.get("done") for e in events)
    tokens = sum(1 for e in events if "token" in e)
    _print(f"run_stream events={len(events)} statuses={statuses[:6]}... done={done} token_events={tokens}")
    if not done:
        tail = events[-4:] if events else []
        _print(f"  run_stream tail: {tail}")
    _print("sleep 6s for OTLP batch flush...")
    time.sleep(6)


# ── Jaeger 树 ─────────────────────────────────────────
def _jaeger_traces() -> list[dict]:
    code, body, _ = _req("GET", JAEGER_UI + "/api/traces?service=character-distill&lookback=1h&limit=200")
    assert code == 200, f"jaeger {code}: {body[:200]!r}"
    return json.loads(body).get("data", [])


def _tree_text(trace: dict, root_name: str) -> list[str]:
    spans = trace.get("spans", [])
    processes = trace.get("processes", {})
    by_id = {s["spanID"]: s for s in spans}
    roots = [s for s in spans if not s.get("references")]
    root = next((s for s in roots if s.get("operationName") == root_name), roots[0] if roots else None)
    if root is None:
        return [f"(no root {root_name}; {len(spans)} spans)"]
    children: dict[str, list[dict]] = {}
    for s in spans:
        refs = s.get("references") or []
        pid = None
        for r in refs:
            if r.get("refType") == "CHILD_OF":
                pid = r.get("spanID")
        children.setdefault(pid, []).append(s)

    def dur_ms(s: dict) -> float:
        return (s.get("duration", 0)) / 1000.0  # Jaeger duration 单位 µs

    def _fmt(s: dict) -> str:
        attrs = {a["key"]: a.get("value") for a in s.get("tags", [])}
        tool = attrs.get("gen_ai.tool.name", "")
        wf = attrs.get("app.workflow", "")
        model = attrs.get("gen_ai.request.model", "")
        extra = " ".join(x for x in [f"tool={tool}" if tool else "", f"wf={wf}" if wf else "",
                                     f"model={model}" if model else ""] if x)
        return f"{dur_ms(s):8.1f}ms  {s['operationName']}  {extra}"

    lines: list[str] = []

    def walk(s: dict, depth: int) -> None:
        lines.append("  " * depth + _fmt(s))
        for c in sorted(children.get(s["spanID"], []), key=lambda c: c.get("startTime", 0)):
            walk(c, depth + 1)

    walk(root, 0)
    # 根外孤儿（同 trace 但没挂根下）——§5 一致性重点
    for s in spans:
        if s["spanID"] not in by_id:  # not a root but referenced? handled
            pass
    if len(roots) > 1:
        extra = [r.get("operationName") for r in roots if r.get("operationName") != root_name]
        lines.append(f"  [extra roots: {extra}]")
    return lines


def cmd_tree(_args) -> None:
    traces = _jaeger_traces()
    _print(f"jaeger traces: {len(traces)}")
    wanted = _state().get("trace_roots") or ["chat.invoke_agent", "distill.stream", "distill.task"]
    shown = 0
    for tr in traces:
        spans = tr.get("spans", [])
        names = {s.get("operationName") for s in spans}
        root_op = next((r for r in wanted if r in names), None)
        if root_op is None:
            continue
        shown += 1
        _print(f"\n===== trace {tr.get('traceID')}  root~{root_op}  spans={len(spans)} =====")
        for line in _tree_text(tr, root_op):
            _print("   " + line)
    if shown == 0:
        _print("no matching traces yet — run drive-chat/drive-distill first")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["up", "down", "drive-chat", "drive-distill", "drive-bg", "tree"])
    args = ap.parse_args()
    globals()["cmd_" + args.cmd.replace("-", "_")](args)


if __name__ == "__main__":
    main()
