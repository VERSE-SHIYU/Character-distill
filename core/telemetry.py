# -*- coding: utf-8 -*-
"""OTel GenAI 手工埋点（②④ Step 2）。集中全部 OTel 逻辑，各埋点文件只加薄装饰器/一行。

开关与零开销：
- OTEL_ENABLED 关（默认）→ 本模块不 import opentelemetry、不建 provider、装饰器原样返回函数、
  span()/trace_sse() 为 no-op —— 调用方行为与未埋点前逐字节一致，只多一层空函数转发。
- OTEL_ENABLED=1 才惰性 import sdk 并建 provider。

命名隔离：代码只写 CANONICAL 短键；导出时 NormalizingExporter 按 ATTR_RENAME 映射成
gen_ai.* 最终名。GenAI semconv 仍是 Development 态（可能改名）——届时只改 ATTR_RENAME。
内容合规：完整 prompt/回复绝不进 span 属性。如需捕获只能用事件，且 OTEL_CAPTURE_CONTENT=1
才生效（默认关）；默认路径无任何用户内容写入。
"""
from __future__ import annotations

import functools
import inspect
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

_ENABLED = os.getenv("OTEL_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
_CAPTURE = os.getenv("OTEL_CAPTURE_CONTENT", "").strip().lower() in ("1", "true", "yes", "on")
_USE_MEMORY = os.getenv("OTEL_EXPORTER", "").strip().lower() == "memory"

# ── canonical 键（代码内部使用）→ gen_ai.* / app.* 最终名 ──────────
A = {
    "op": "gen_ai.operation.name",       # 见 spec §1.1：invoke_agent/plan/execute_tool/chat/embeddings
    "model": "gen_ai.request.model",
    "ptok": "gen_ai.usage.input_tokens",
    "ctok": "gen_ai.usage.output_tokens",
    "err": "error.type",
    "wf": "app.workflow",                 # chat / distill 分链路
    "ttft": "app.ttft_ms",                # SSE 首 token 时延
    "tool": "gen_ai.tool.name",
    "retrieval_hits": "app.retrieval.hits",
    "degraded": "agent.degraded",         # AgentLoop 降级回退标记（bool）
    "repair_stage": "distill.json_repair.stage",  # JSON 修复命中阶段 1/2/3
}
# GenAI semconv 改名时只改这里（导出期归一化，见 NormalizingExporter）
ATTR_RENAME = dict(A)

_tracer: Any = None
_SPANS: list[Any] = []  # OTEL_EXPORTER=memory 时收集，供 §4 断言


def enabled() -> bool:
    return _ENABLED


def capture_enabled() -> bool:
    return _CAPTURE


def _setup() -> Any:
    """惰性建 provider（仅 OTEL_ENABLED=1 首次调用）。返回 tracer 或 None。

    Exporter 由 OTEL_EXPORTER 选择：
    - memory        → 进程内收集（§4 断言）
    - otlp/otlp_http→ OTLP HTTP 到 OTEL_EXPORTER_OTLP_ENDPOINT（Jaeger，Batch 防压测卡单 span）
    - 缺省/其他     → console
    """
    global _tracer
    if _tracer is not None or not _ENABLED:
        return _tracer
    from opentelemetry import trace as _ot_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    # service.name 固定，Jaeger/Step4 按此分组；不随 OTEL_SERVICE_NAME 变
    provider = TracerProvider(resource=Resource.create({"service.name": "character-distill"}))
    if _USE_MEMORY:
        provider.add_span_processor(SimpleSpanProcessor(_MemoryExporter(_SPANS)))
    else:
        exp_mode = os.getenv("OTEL_EXPORTER", "console").strip().lower()
        if exp_mode in ("otlp", "otlp_http"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as _OTLP
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            # 不传 endpoint：由 exporter 读 OTEL_EXPORTER_OTLP_ENDPOINT 并自动拼 /v1/traces；
            # 显式传 endpoint 会跳过拼路径，POST 到根路径得 404。
            provider.add_span_processor(BatchSpanProcessor(_NormalizingExporter(_OTLP())))
        else:
            provider.add_span_processor(SimpleSpanProcessor(_NormalizingExporter(ConsoleSpanExporter())))
    _ot_trace.set_tracer_provider(provider)
    _tracer = _ot_trace.get_tracer("character-distill")
    return _tracer


class _MemoryExporter:
    """内存 exporter：把导出的 span 收进外部 list（span.attributes 已归一化）。"""

    def __init__(self, sink: list) -> None:
        self._sink = sink

    def export(self, spans) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult
        for s in spans:
            _apply_rename(s)
            self._sink.append(s)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover
        self._sink.clear()


class _NormalizingExporter:
    """导出期归一化：canonical → gen_ai.*，隔离 GenAI semconv 变动。"""

    def __init__(self, inner) -> None:
        self._inner = inner

    def export(self, spans) -> Any:
        for s in spans:
            _apply_rename(s)
        return self._inner.export(spans)

    def shutdown(self) -> None:  # pragma: no cover
        self._inner.shutdown()


def _apply_rename(span: Any) -> None:
    # ReadableSpan.attributes 是 mappingproxy（只读），底层可变存储是 _attributes
    # （BoundedAttributes）——导出归一化改在底层做，映射 proxy 会自动反映。
    raw = getattr(span, "_attributes", None)
    if raw is None:
        attrs = getattr(span, "attributes", None)
        if attrs:
            raw = dict(attrs)
        else:
            return
    old = dict(raw)
    for k in old:
        del raw[k]
    for k, v in old.items():
        raw[ATTR_RENAME.get(k, k)] = v


def tracer():
    return _setup()


def spans() -> list:
    """OTEL_EXPORTER=memory 时返回已采集 span 列表（§4 断言用）。"""
    return list(_SPANS)


def reset_spans() -> None:
    _SPANS.clear()


# ── span 辅助 ─────────────────────────────────────────────
def _span_cm(name: str, op: str | None = None, wf: str | None = None, attrs: dict | None = None):
    tr = _setup()
    if tr is None:
        return _null_cm()
    # 全部写入 canonical 短键；最终 gen_ai.* 名只在导出期由 ATTR_RENAME 映射
    merged = {}
    if op is not None:
        merged["op"] = op
    if wf is not None:
        merged["wf"] = wf
    if attrs:
        merged.update(attrs)
    return tr.start_as_current_span(name, attributes=merged)


@contextmanager
def span(name: str, *, op: str | None = None, wf: str | None = None, attrs: dict | None = None):
    """上下文 span；OTEL 关时为 no-op（yield None）。"""
    cm = _span_cm(name, op, wf, attrs)
    with cm as sp:
        yield sp


class _null_cm:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def set_usage(sp, prompt_tokens: int | None, completion_tokens: int | None) -> None:
    if sp is None:
        return
    if prompt_tokens is not None:
        sp.set_attribute("ptok", int(prompt_tokens))
    if completion_tokens is not None:
        sp.set_attribute("ctok", int(completion_tokens))


def set_error(sp, exc: BaseException) -> None:
    if sp is None:
        return
    sp.set_attribute("err", type(exc).__name__)


def set_attr(sp, key: str, value: Any) -> None:
    """写 canonical 短键属性（导出时归一到 gen_ai.*）。"""
    if sp is None:
        return
    sp.set_attribute(key, value)


def set_current_attr(key: str, value: Any) -> None:
    """写当前 context 中 span 的 canonical 属性；无 recording span 时为 no-op。

    用于无法拿到显式 span 引用的埋点（装饰器作用域外的降级/修复判定分支）。
    """
    if not _ENABLED:
        return
    from opentelemetry import trace as _ot_trace
    sp = _ot_trace.get_current_span()
    if sp.is_recording():
        sp.set_attribute(key, value)


def capture_event(sp, name: str, text: str) -> None:
    """内容捕获：仅 OTEL_CAPTURE_CONTENT=1 生效（事件，非属性），默认关。"""
    if sp is None or not _CAPTURE:
        return
    sp.add_event(name, {"text": text})


def _start_noncurrent(name: str, op: str | None, wf: str | None) -> Any:
    """开一个不设为 current 的 span：parent 从当前 context 继承，但不在存续期 attach。

    流式生成器可能被 asyncio.to_thread 逐片推进——每次 to_thread 都
    copy_context() 一个新 context，若用 start_as_current_span，span 在 ctx#1 开、
    ctx#N 关，detach 会因 token 属另一 Context 抛 ValueError（实测）。流式 span 无需
    作为 current（token 层无子 span），只需正确 parent + usage，故手动 end 即够。
    """
    tr = _setup()
    if tr is None:
        return None
    merged = {}
    if op is not None:
        merged["op"] = op
    if wf is not None:
        merged["wf"] = wf
    return tr.start_span(name, attributes=merged)


# ── 装饰器：值函数 / 生成器函数 ──────────────────────────
def spanned(name: str, *, op: str | None = None, wf: str | None = None,
            finalize: Callable | None = None):
    """包一层 span。OTEL 关时原样返回原函数（零开销）。

    finalize(sp, self, result, exc)：span 收尾前调用，用于补 usage/属性。
    """

    def deco(fn):
        if not _ENABLED:
            return fn
        # 值函数与生成器函数拆两个 wrapper：body 含 yield from 会让 Python
        # 把整个函数编译成生成器，值函数会变成惰性（return 不执行）。
        if inspect.isgeneratorfunction(fn):
            @functools.wraps(fn)
            def _gen_wrapper(*args, **kwargs):
                sp = _start_noncurrent(name, op, wf)
                try:
                    result = yield from fn(*args, **kwargs)
                except GeneratorExit:
                    if sp is not None:
                        sp.end()  # 中途 close（如客户端断流）不泄漏 span
                    raise
                except Exception as exc:
                    if sp is not None:
                        set_error(sp, exc)
                        if finalize:
                            finalize(sp, args[0] if args else None, None, exc)
                        sp.end()
                    raise
                if sp is not None:
                    if finalize:
                        finalize(sp, args[0] if args else None, result, None)
                    sp.end()
                return result

            return _gen_wrapper

        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            cm = _span_cm(name, op, wf)
            with cm as sp:
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    set_error(sp, exc)
                    if finalize:
                        finalize(sp, args[0] if args else None, None, exc)
                    raise
                if finalize:
                    finalize(sp, args[0] if args else None, result, None)
                return result

        return _wrapper

    return deco


def async_spanned(name: str, *, op: str | None = None, wf: str | None = None,
                  finalize: Callable | None = None):
    """协程函数包 span（async LLM 调用的 map 分片等）。OTEL 关时原样返回原函数（零开销）。

    finalize(sp, self, result, exc)：与 spanned 同签名；result 为协程返回值。

    与 spanned 的关键差异：span 用 _start_noncurrent（start_span，不设 current、无 attach/detach），
    因为协程可能在 asyncio.run 的 task 里执行（ctx_thread 内 asyncio.run 并发 map），
    start_as_current_span 的 attach/detach 跨 await/task 边界会重现已修的 detach token 错配。
    LLM 调用是叶子（无子 span），不设 current 安全——parent 从当前 context 继承即可挂到调用方。
    """

    def deco(fn):
        if not _ENABLED:
            return fn

        @functools.wraps(fn)
        async def _async_wrapper(*args, **kwargs):
            sp = _start_noncurrent(name, op, wf)
            self_obj = args[0] if args else None
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                if sp is not None:
                    set_error(sp, exc)
                    if finalize:
                        finalize(sp, self_obj, None, exc)
                    sp.end()
                raise
            if sp is not None:
                if finalize:
                    finalize(sp, self_obj, result, None)
                sp.end()
            return result

        return _async_wrapper

    return deco


def ctx_thread(target: Callable, args: tuple = (), kwargs: dict | None = None,
               *, daemon: bool = True, name: str | None = None) -> threading.Thread:
    """裸线程 + OTel context 传播：把当前 OTel Context attach 进子线程再执行。

    用 opentelemetry.context.attach/detach 而非 contextvars.copy_context().run()——
    copy_context().run() 在 copy 出的隔离 Context 里 set token，target 结束后 detach
    会因 token 属另一个 Context 抛 ValueError（实测 Python 3.12）。attach 在同线程原生
    context 里 set/detach，token 配对正确。OTEL 关时退化为普通 daemon 线程（零开销）。
    """
    if kwargs is None:
        kwargs = {}
    if not _ENABLED:
        return threading.Thread(target=target, args=args, kwargs=kwargs, daemon=daemon, name=name)
    from opentelemetry.context import attach, detach, get_current

    otel_ctx = get_current()

    def _t():
        token = attach(otel_ctx)
        try:
            target(*args, **kwargs)
        finally:
            detach(token)

    return threading.Thread(target=_t, daemon=daemon, name=name)


def ctx_submit(pool, fn: Callable, *args, **kwargs):
    """ThreadPoolExecutor.submit + context 传播（实测裸 submit 不拷 contextvar）。"""
    if not _ENABLED:
        return pool.submit(fn, *args, **kwargs)
    from opentelemetry.context import attach, detach, get_current

    otel_ctx = get_current()

    def _job():
        token = attach(otel_ctx)
        try:
            return fn(*args, **kwargs)
        finally:
            detach(token)

    return pool.submit(_job)


async def trace_sse_async(agen, name: str, *, op: str = None, wf: str = None):
    """SSE 异步生成器包 span：测 TTFT 并区分 workflow。

    TTFT = 根 span 打开(开始消费 SSE 响应体) → 首个 SSE 数据块。
    anchor 在 span 打开的同刻采集（单调钟与 span 生命周期对齐），故恒有
    ttft ≤ 根 span duration——不会再因 handler 预置耗时 / 响应排队延迟把
    ttft 推到 span 边界之外（曾见 ttft > root dur 的脏数据）。

    OTEL 关时直接 yield from（零开销）。
    """
    tr = _setup()
    if tr is None:
        async for item in agen:
            yield item
        return
    cm = _span_cm(name, op, wf)
    with cm as sp:
        anchor_ms = time.monotonic() * 1000  # 与根 span 起点同一物理时刻
        first = True
        try:
            async for item in agen:
                if first:
                    sp.set_attribute("ttft", int((time.monotonic() * 1000) - anchor_ms))
                    first = False
                yield item
        except Exception as exc:
            set_error(sp, exc)
            raise


__all__ = [
    "enabled", "capture_enabled", "spans", "reset_spans", "tracer",
    "span", "spanned", "async_spanned", "ctx_thread", "ctx_submit",
    "trace_sse_async", "set_usage", "set_error", "set_attr", "capture_event", "A",
]
