# -*- coding: utf-8 -*-
"""OTel trace 完整性断言（②④ Step 2 §4）。HTTP 级、覆盖真实跨线程边界。

telemetry 的 OTEL_ENABLED / OTEL_EXPORTER 在模块 import 期冻结（env 快照），共享
pytest 进程里先 import 的一方会污染其余用例 → 让子脚本 tests/perf/trace_assert_child.py
在全新子解释器里跑（OTEL_ENABLED=1 + OTEL_EXPORTER=memory），本父进程只 spawn 并断言输出。

覆盖：
1. ctx_thread / ctx_submit 让线程内子 span 挂到调用方 trace；对照组裸
   ThreadPoolExecutor.submit 产孤儿 —— 证明边界真实、包装是负载。
2. agent 骨架：plan → execute_tool 两层在树内（确定性 stub，无网络）。
3. chat HTTP/SSE：invoke_agent 根 → llm.chat_stream 在 asyncio.to_thread worker
   线程里建 span 且不孤儿（真实线程边界）。全 span 无孤儿、同 trace。
4. distill SSE 根 wf=distill → chat/distill 可分链路。
5. async map 分片：async_chat 协程 span 跨 ctx_thread+asyncio.run 挂根、带 usage
   （async_spanned 回归护栏，防重现已修的 detach 错配）。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHILD = os.path.join(ROOT, "tests", "perf", "trace_assert_child.py")


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["OTEL_ENABLED"] = "1"
    env["OTEL_EXPORTER"] = "memory"
    env["OTEL_CAPTURE_CONTENT"] = "0"
    return env


@pytest.mark.skipif(not os.path.exists(CHILD), reason="child assertion script missing")
def test_trace_completeness_via_child_interpreter() -> None:
    """子解释器跑完整 trace 断言：无孤儿、无断链、跨线程边界真实。"""
    proc = subprocess.run(
        [sys.executable, CHILD],
        cwd=ROOT,
        env=_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-40:])
    assert proc.returncode == 0, f"child failed rc={proc.returncode}\n{tail}"
    assert "TRACE ASSERT PASS" in proc.stdout, f"assertions not all green\n{tail}"
    assert "PASS 20 / 20" in proc.stdout, f"expected 20 checks\n{tail}"
    # 流式 span 曾因跨 to_thread 的 detach token 错配抛 ValueError（stderr）——回归护栏
    assert "Failed to detach context" not in proc.stdout, f"otel detach regression\n{tail}"
