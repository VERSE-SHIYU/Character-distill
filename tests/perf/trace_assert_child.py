# -*- coding: utf-8 -*-
"""OTel trace-完整性断言（②④ Step 2 §4）。父级 tests/test_trace_completeness.py 以
全新子解释器运行（OTEL_ENABLED=1 + OTEL_EXPORTER=memory），避免共享 pytest 进程里
telemetry 模块 env 在 import 期冻结导致的跨测试污染。

断言（HTTP/SSE 请求边界 + 真实跨线程）：
1. ctx_thread / ctx_submit 让线程内子 span 挂到调用方 trace；对照组裸
   ThreadPoolExecutor.submit 产孤儿 —— 证明边界真实、包装是负载。
2. chat SSE：invoke_agent 根(wf=chat+ttft) → llm.chat_stream 在 asyncio.to_thread
   worker 线程里建 span 且不孤儿（真实线程边界）。全 span 无孤儿、同 trace。
3. distill SSE 根 wf=distill → chat/distill 可分链路。
4. agent 链路骨架：plan → execute_tool 两层在树内。

用法：python tests/perf/trace_assert_child.py （设 OTEL_ENABLED=1 OTEL_EXPORTER=memory）
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

assert os.getenv("OTEL_ENABLED", "").lower() in ("1", "true", "yes", "on"), "child needs OTEL_ENABLED=1"
assert os.getenv("OTEL_EXPORTER", "") == "memory", "child needs OTEL_EXPORTER=memory"

from core import telemetry as T  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_mock() -> tuple[subprocess.Popen, str]:
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "tests", "perf", "mock_llm_server.py"),
         "--port", str(port), "--preset", "fast"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
    )
    base = f"http://127.0.0.1:{port}/v1"
    # 全量 pytest 下进程/import 负载高，启动可达数秒 → 放宽就绪窗口（最长 ~20s）
    for _ in range(400):
        try:
            urllib.request.urlopen(base.rstrip("/v1") + "/health", timeout=0.5)
            return proc, base
        except Exception:
            if proc.poll() is not None:
                raise SystemExit(f"mock server died: {(proc.stdout.read() if proc.stdout else '')[:500]}")
            time.sleep(0.05)
    raise SystemExit("mock server not ready")


def stop_mock(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def make_engine(base: str):
    from adapters.llm_adapter import LLMAdapter
    from core.chat_engine import ChatEngine
    from core.schema import CharacterCard
    llm = LLMAdapter(api_key="mock", base_url=base, model="deepseek-v4-pro", temperature=0, max_tokens=24)
    engine = ChatEngine(llm=llm, rag=None, card=CharacterCard(name="测试卡"), card_id="trace-card")
    engine._storage = None  # 关 usage 后台线程（storage 缺省 → try_record_usage 直接返回）
    engine._user_id = ""
    return engine


def tree_summary() -> dict:
    """把采集到的 span 归成 {span_id: (name, parent_id, attrs)}，按 name 索引。"""
    spans = T.spans()
    by_id = {s.context.span_id: s for s in spans}
    names = {}
    for s in spans:
        names.setdefault(s.name, []).append(s)
    orphans = [
        s.name for s in spans
        if s.parent is not None and s.context.trace_id != s.parent.trace_id  # parent 不同 trace
    ]
    # parent_span_id None → root；否则 parent 必须在本 trace 内存在
    bad = []
    root_ids = set()
    for s in spans:
        p = s.parent
        if p is None:
            root_ids.add(s.context.span_id)
        else:
            if p.trace_id != s.context.trace_id:
                bad.append(f"{s.name}:parent-cross-trace")
            elif p.span_id not in by_id:
                bad.append(f"{s.name}:parent-missing")
    trace_ids = {s.context.trace_id for s in spans}
    return {"spans": spans, "by_id": by_id, "names": names, "roots": root_ids,
            "bad": bad, "orphan_names": orphans, "traces": trace_ids}


def assert_ctx_boundary() -> None:
    """ctx_thread / ctx_submit 传播 vs 裸 executor.submit 孤儿（对照组）。"""
    T.reset_spans()
    with T.span("root.boundary"):
        from concurrent.futures import ThreadPoolExecutor

        def child(name: str) -> None:
            with T.span(name):
                pass

        th = T.ctx_thread(child, ("thr.child",), daemon=True)
        th.start(); th.join()
        with ThreadPoolExecutor(max_workers=2) as pool:
            T.ctx_submit(pool, child, "ex.child").result()
            pool.submit(child, "ex.orphan").result()  # 对照组：裸 submit 不拷 context
    root = [s for s in T.spans() if s.name == "root.boundary"]
    root_id = root[0].context.span_id if root else -1

    def parent_is_root(s):
        return s.parent is not None and s.parent.span_id == root_id and s.context.trace_id == root[0].context.trace_id

    ctx_ok = [s for s in T.spans() if s.name in ("thr.child", "ex.child")]
    orphan = [s for s in T.spans() if s.name == "ex.orphan"]
    check("ctx_thread/ctx_submit 子 span 挂在根 trace 下", len(ctx_ok) == 2 and all(parent_is_root(s) for s in ctx_ok),
          f"ctx_ok={[s.name for s in ctx_ok]}")
    check("对照组裸 executor.submit 产孤儿(证明边界真实)", len(orphan) == 1 and orphan[0].parent is None,
          f"orphan={[s.name for s in orphan]}")


def _child_span(name: str, sink: list) -> None:
    with T.span(name):
        sink.append(1)


def assert_chat_sse(base: str) -> None:
    """真实 HTTP SSE 边界：invoke_agent 根 → chat_stream 推理 span（to_thread worker 线程）。"""
    import asyncio
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/sse")
    async def _sse():
        engine = make_engine(base)

        def _next_piece(stream_obj):
            try:
                return next(stream_obj), False
            except StopIteration:
                return "", True

        async def _gen():
            try:
                stream = engine.chat_stream("你好，测试。", voice_mode=False)
                while True:
                    piece, done = await asyncio.to_thread(_next_piece, stream)
                    if done:
                        break
                    if piece:
                        yield f"data: {json.dumps({'token': piece}, ensure_ascii=False)}\n\n"
            finally:
                pass

        return StreamingResponse(
            T.trace_sse_async(_gen(), "chat.invoke_agent", op="invoke_agent", wf="chat"),
            media_type="text/event-stream",
        )

    T.reset_spans()
    with TestClient(app) as client:
        r = client.post("/sse")
    body = r.text
    has_token = "token" in body
    check("HTTP SSE 200 且产出 token", r.status_code == 200 and has_token, f"status={r.status_code} len={len(body)}")

    su = tree_summary()
    spans = su["spans"]
    roots = [s for s in spans if s.parent is None]
    root = [s for s in roots if s.name == "chat.invoke_agent"]
    check("存在 invoke_agent 根 span(HTTP/SSE 边界)", len(root) == 1, f"roots={[s.name for s in roots]}")
    if root:
        ra = dict(root[0].attributes)
        check("根带 wf=chat 与 op=invoke_agent", ra.get(T.A["wf"]) == "chat" and ra.get(T.A["op"]) == "invoke_agent", str(ra))
        check("根带 ttft_ms(≥0)", T.A["ttft"] in ra and ra[T.A["ttft"]] >= 0, f"ttft={ra.get(T.A['ttft'])}ms")
    chat_spans = [s for s in spans if s.name == "llm.chat_stream"]
    check("chat 推理 span(llm.chat_stream) 存在", len(chat_spans) >= 1, f"n={len(chat_spans)}")
    if chat_spans:
        ca = dict(chat_spans[0].attributes)
        check("推理 span 有 model+usage",
              ca.get(T.A["model"]) == "deepseek-v4-pro" and T.A["ptok"] in ca and T.A["ctok"] in ca, str(ca))
    # 每个推理 span 的 parent 指向 invoke_agent 根（跨 to_thread 线程不孤儿）
    attach_ok = True
    for cs in chat_spans:
        p = cs.parent
        if p is None or p.span_id != (root[0].context.span_id if root else -1):
            attach_ok = False
    check("chat 推理 span 挂到 invoke_agent 根(真实线程边界不孤儿)", attach_ok and len(chat_spans) >= 1)

    check("全 span 无孤儿(每条 parent 在本 trace 内存在)",
          su["bad"] == [] and len(su["traces"]) == 1, f"bad={su['bad']} traces={su['traces']}")
    check("存在 context.build(装饰器锚点)", "context.build" in su["names"])


def assert_distill_wf() -> None:
    """distill SSE 根带 wf=distill → chat/distill 可分链路。"""
    import asyncio
    async def _agen():
        yield "x"

    async def _run():
        T.reset_spans()
        out = []
        async for i in T.trace_sse_async(_agen(), "distill.stream", wf="distill"):
            out.append(i)
        return out
    assert asyncio.run(_run()) == ["x"]
    su = tree_summary()
    dist = [s for s in su["spans"] if s.name == "distill.stream"]
    check("distill 根 wf=distill", len(dist) == 1 and dict(dist[0].attributes).get(T.A["wf"]) == "distill")


def assert_agent_skeleton() -> None:
    """agent 链路骨架：plan → execute_tool 两层在树内（确定性 stub，无网络）。"""
    from core.agent.agent_loop import AgentLoop
    from core.agent.tools import AgentToolkit

    class _ToolCall:
        def __init__(self, name):
            self.function = SimpleNamespace(name=name, arguments='{"query": "角色经历"}')
            self.id = "call_0"

        def model_dump(self):
            return {"id": self.id, "type": "function", "function": {"name": self.function.name, "arguments": self.function.arguments}}

    class _StubLLM:
        def __init__(self):
            self._calls = 0

        def chat_with_tools(self, system_prompt, messages, tools):
            self._calls += 1
            if self._calls == 1:
                return SimpleNamespace(content=None, tool_calls=[_ToolCall("web_search")])
            return SimpleNamespace(content="ok", tool_calls=[])

    class _StubCtx:
        """离线 ctx 替身：三路检索直接返回固定文本，无网络。"""
        def _retrieve_scenes(self, query: str) -> str:
            return "（stub 场景）"
        def _retrieve_memories(self, query: str, current_mood: str | None = None) -> str:
            return "（stub 记忆）"
        def _search_web(self, query: str) -> str:
            return "（stub 网页）"

    T.reset_spans()
    with T.span("root.agent"):
        result = AgentLoop(_StubLLM(), AgentToolkit(_StubCtx(), current_mood="平静")).run("角色背景", [{"role": "user", "content": "hi"}])
    check("agent run 返回非 degraded", not result.degraded, f"steps={len(result.steps)}")
    su = tree_summary()
    plan = [s for s in su["spans"] if s.name == "agent.plan"]
    et = [s for s in su["spans"] if s.name == "agent.execute_tool"]
    check("plan span 存在(op=plan)", len(plan) == 1 and dict(plan[0].attributes).get(T.A["op"]) == "plan")
    check("execute_tool span 存在且带 tool 名",
          len(et) == 1 and dict(et[0].attributes).get(T.A["tool"]) == "web_search", f"n={len(et)}")
    # execute_tool 的 parent 应为 plan（或同 trace）
    if et and plan:
        p = et[0].parent
        check("execute_tool 挂在 plan 下", p is not None and p.span_id == plan[0].context.span_id)
    check("agent 树无孤儿", su["bad"] == [], str(su["bad"]))


def assert_async_boundary(base: str) -> None:
    """异步协程 span 跨 ctx_thread + asyncio.run 边界（async_spanned 回归护栏）。

    复刻 distill map 分片形态：后台 ctx_thread 里 asyncio.run() 并发 N 个 async_chat
    （各自独立 AsyncOpenAI client）。断言每个 llm.chat 协程 span 挂到外层根 span，
    带 model+usage，无孤儿 —— 证明 async 变体不重现跨 context detach 错配。
    """
    import asyncio
    from adapters.llm_adapter import LLMAdapter

    llm = LLMAdapter(api_key="mock", base_url=base, model="deepseek-v4-pro",
                     temperature=0, max_tokens=24)
    root_name = "root.asyncmap"
    N = 3

    def _worker() -> None:
        async def _map() -> None:
            run_client = llm._make_async_client()
            try:
                async def _one(i: int) -> None:
                    await llm.async_chat(
                        "你是测试角色。",
                        [{"role": "user", "content": f"分片{i}"}],
                        client=run_client,
                    )
                await asyncio.gather(*[asyncio.create_task(_one(i)) for i in range(N)])
            finally:
                await run_client.close()
        asyncio.run(_map())

    T.reset_spans()
    with T.span(root_name):
        th = T.ctx_thread(_worker, daemon=True)
        th.start()
        th.join()

    su = tree_summary()
    root = [s for s in su["spans"] if s.name == root_name]
    ch = [s for s in su["spans"] if s.name == "llm.chat"]
    if root:
        rid = root[0].context.span_id
        parent_ok = all(
            s.parent is not None and s.parent.span_id == rid
            and s.context.trace_id == root[0].context.trace_id
            for s in ch
        )
    else:
        parent_ok = False
    check(f"async map 分片 span(llm.chat)≥{N} 挂根下(跨 ctx_thread+asyncio.run)",
          len(root) == 1 and len(ch) >= N and parent_ok, f"n={len(ch)}")
    attrs = [dict(s.attributes) for s in ch]
    check("async 分片 span 带 model+usage",
          len(ch) >= N and all(a.get(T.A["model"]) == "deepseek-v4-pro" for a in attrs)
          and all(T.A["ptok"] in a and T.A["ctok"] in a for a in attrs),
          f"model_ok={all(a.get(T.A['model'])=='deepseek-v4-pro' for a in attrs)}")
    check("async 树无孤儿、单 trace", su["bad"] == [] and len(su["traces"]) == 1,
          f"bad={su['bad']} traces={su['traces']}")


def main() -> int:
    mock, base = None, None
    try:
        assert_ctx_boundary()
        assert_agent_skeleton()
        mock, base = start_mock()
        assert_chat_sse(base)
        assert_async_boundary(base)
        assert_distill_wf()
    finally:
        if mock:
            stop_mock(mock)
    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        return 1
    print("TRACE ASSERT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
