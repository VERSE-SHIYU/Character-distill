"""基于 OpenAI SDK 的 DeepSeek 兼容接口封装。"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import os

import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI, BadRequestError, OpenAI

from core import telemetry as T  # OTel 埋点（OTEL_ENABLED 关时装饰器原样返回，零开销）


def _classify_retry(exc: Exception) -> tuple[bool, float | None]:
    """返回 (是否为429限流, Retry-After秒数或None)。"""
    is_429 = False
    # openai.RateLimitError may not be importable everywhere, check by name
    if type(exc).__name__ == "RateLimitError" and "openai" in type(exc).__module__:
        is_429 = True
    else:
        is_429 = getattr(exc, "status_code", None) == 429 or "429" in str(exc)

    retry_after = None
    if is_429:
        try:
            headers = getattr(exc, "response", None).headers if hasattr(exc, "response") else None
            if headers:
                val = headers.get("Retry-After", "")
                if val and val.isdigit():
                    retry_after = min(float(val), 60.0)
        except Exception:
            pass
    return is_429, retry_after


# ── 阶段 D 重试预算组（唯一重试控制点参数）────────────────────────────
# 缺陷 #1（retry 嵌套）：旧 chat/async_chat/chat_with_tools 各复制一份 budget×SDK 默认
# max_retries=2，至多 9 次 HTTP；非 429 退避 (attempt+1)*5s → 决策轮 degrade 前固定烧 5s+10s。
# 现：SDK max_retries 归 0（见 __init__），次数与总墙钟都在 _RetryBudget 封顶（先到先弃）。
#   attempts     非 429 失败的硬次数上限
#   deadline_s   budget 起算的总墙钟上限。决策 6s / 生成 60s / 流式首 token 8s。
#                on_failure 把 wait clamp 到 max_wait=剩余−(margin+min)，保下一次 attempt 至少
#                有 margin+min 的窗（D1a 审计必修1）；并在 sleep 前预计算下一次超时 _next_to，
#                免疫 sleep 过冲把「刚好够窗」抖成「不够」（D1b 确定性，紧界回归不闪断）。
#   ceiling_s    role ceiling：单次 create 超时上限（决策 5 / 生成 45 / 流式 7），再被剩余窗夹逼。
#                D1b：每次 create 传 timeout = min(ceiling, 剩余−margin)（见 attempt_timeout）；
#                剩余 < margin+min 撑不起一次有效 attempt → attempt_timeout 直接抛，不发出会
#                假失败的死亡窗 create（max(0.25, 0−1)=0.25s 那类误导性超时）。
# 决策轮=路由：失败应快速降级 → 2 次 / 6s / 退避 1s（旧 5s+10s）。
# 生成轮=交付物：值得多试 → 3 次 / 60s / 退避 5s 线性（保留旧节奏，被 deadline 夹逼）。
_DECISION_ATTEMPTS = 2
_DECISION_DEADLINE_S = 6.0
_DECISION_BACKOFF_S = 1.0
_GEN_ATTEMPTS = 3
_GEN_DEADLINE_S = 60.0
_GEN_BACKOFF_S = 5.0
# 流式首 token 补偿：SDK 重试撤除后 create()（吐 chunk 前）连接失败不再被 SDK 静默重试，
# 折叠到同一 _RetryBudget（≤_STREAM_ATTEMPTS / ≤_STREAM_DEADLINE_S / 退避 1s）。
# 一旦已 yield 内容即不可安全重放，只包 create() 返回前。
_STREAM_ATTEMPTS = 2
_STREAM_DEADLINE_S = 8.0
_STREAM_BACKOFF_S = 1.0
# 429 独立次数上限（旧 rate_limit_budget=5 语义，D1a 审计必修2）：429 不计入非429 attempts，
# 但需自身上限，否则 Retry-After 极小(如 0.1)时决策轮 6s 内可高频重打数十次 → 加速 provider
# 侧用户 key 封禁。两个计数器、两个上限、共享一个 deadline。
_RATE_LIMIT_ATTEMPTS = 5
# D1b per-attempt timeout 组：
#   _*_ATTEMPT_S           role ceiling（单次 create 超时上限），决策 5 / 生成 45 / 流式 7。
#                          三个 ceiling 均可 env 覆盖（LLM_DECISION_ATTEMPT_S /
#                          LLM_GEN_ATTEMPT_S / LLM_STREAM_ATTEMPT_S，默认值不变，同
#                          CARD_GUARD_ENABLED 模式）——生产发现太紧改环境变量即可，不发版。
#   _ATTEMPT_TIMEOUT_MARGIN_S  超时触发(on_failure 裁决)须落在 deadline 内的收尾余量
#   _ATTEMPT_MIN_S             一次有效 attempt 的最小超时；剩余连 margin+min 都撑不起则拒发
_ATTEMPT_TIMEOUT_MARGIN_S = 1.0
_ATTEMPT_MIN_S = 0.25
_ATTEMPT_WINDOW_S = _ATTEMPT_TIMEOUT_MARGIN_S + _ATTEMPT_MIN_S  # 撑起一次 attempt 所需剩余 = 1.25s
# floor=_ATTEMPT_MIN_S：防 0/负 ceiling 把 create(timeout=0) 变 no-timeout，静默撤掉 D1b 单次封顶。
_DECISION_ATTEMPT_S = max(float(os.getenv("LLM_DECISION_ATTEMPT_S", "5.0")), _ATTEMPT_MIN_S)
_GEN_ATTEMPT_S = max(float(os.getenv("LLM_GEN_ATTEMPT_S", "45.0")), _ATTEMPT_MIN_S)
_STREAM_ATTEMPT_S = max(float(os.getenv("LLM_STREAM_ATTEMPT_S", "7.0")), _ATTEMPT_MIN_S)


class _RetryBudget:
    """单次调用的重试预算：非429次数 × 429次数 × 单次超时(ceiling×剩余窗) × 总墙钟，先到先弃（单一裁决点）。

    旧三个方法各自手写 budget 递减 + 退避 + 耗尽的循环，策略漂移又埋了 9× 嵌套乘法。
    本类是唯一重试裁决点：
      on_failure()       判分类(429/其他)、累计、判耗尽、把退避 clamp 到 max_wait=剩余−(margin+min)
                         （保下一次 attempt 有有效窗）、预计算下一次超时 _next_to，返回下次退避
                         秒数或抛 RuntimeError；
      attempt_timeout()  每次 create 前取单次超时 = min(ceiling, 剩余−margin)。有 _next_to（上次
                         on_failure 预计算）先消费它；否则按当下剩余推。剩余 < margin+min 撑不起
                         一次有效 attempt → 直接抛，不发出会假失败的死亡窗 create。
    429 有独立次数上限（rate_limit_attempts，旧 rate_limit_budget=5 语义），共享 deadline。
    """

    def __init__(self, *, attempts: int, deadline_s: float, ceiling_s: float, backoff_mult_s: float,
                 log_prefix: str = "LLMAdapter", err_prefix: str = "LLM API",
                 rate_limit_attempts: int | None = None) -> None:
        self._attempts = attempts
        self._ceiling = ceiling_s
        self._rate_limit_attempts = _RATE_LIMIT_ATTEMPTS if rate_limit_attempts is None else rate_limit_attempts
        self._deadline = time.monotonic() + deadline_s
        self._backoff_mult = backoff_mult_s
        self._tag = f"[{log_prefix}] "
        self._err = err_prefix
        self._non429 = 0
        self._rate_limited = 0
        self._total = 0
        self._next_to: float | None = None  # 下一次 attempt 的预计算超时（on_failure 在 sleep 前定死）
        self._last_to: float | None = None  # 本次 create 实际用的超时（attempt_timeout 记录，on_failure 判边界）

    def remaining_s(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def attempt_timeout(self) -> float:
        """本次 create 应传的 timeout（秒）。剩余撑不起一次有效 attempt 则抛（不发出会假失败的 create）。

        timeout = min(ceiling, 剩余 − margin)：单次请求最坏吃到 剩余−margin，超时触发后还留 margin
        给 on_failure 收尾裁决（不越过 deadline）。若 剩余−margin < min，连最小有效超时都不够，
        强行发会得到 max(0.25, 负)=0.25s 这类几乎必假失败的死亡窗请求 → 直接判 budget 尽。
        后续 attempt 返回 on_failure 预计算的 _next_to（一次即清）：on_failure 已按 remaining−wait−margin
        算好下一次超时，杜绝 sleep 过冲把「刚好够窗」抖成「不够」——否则紧界回归/真机会在发与不发
        最末 0.25s attempt 间非确定摇摆。
        """
        if self._next_to is not None:
            self._last_to = self._next_to
            self._next_to = None
            return self._last_to
        window = self._deadline - _ATTEMPT_TIMEOUT_MARGIN_S - time.monotonic()
        if window < _ATTEMPT_MIN_S:
            raise RuntimeError(
                f"{self._err} no time for an attempt: window {window:.2f}s < min "
                f"{_ATTEMPT_MIN_S:.2f}s (need margin+min {_ATTEMPT_WINDOW_S:.2f}s)")
        self._last_to = min(self._ceiling, window)
        return self._last_to

    def on_failure(self, exc: Exception) -> float:
        """记录一次失败；返回下次尝试前应等秒数，或达上限/窗尽抛 RuntimeError。

        顺序：累计对应计数器 → 判各自上限 → 算退避 → 窗口门。剩余 < margin+min 撑不起一次
        有效 attempt → 直接判 exhausted（不 sleep、也不让调用方去发 attempt_timeout 会拒的
        死亡窗 create）；否则退避最多睡到 max_wait=剩余−(margin+min)（保下一次 attempt 至少有
        margin+min 的窗），并预计算下一次超时 _next_to=min(ceiling, 剩余−wait−margin) ≥ min。
        """
        self._total += 1
        is_429, retry_after = _classify_retry(exc)
        if not is_429:
            self._non429 += 1
            cap_hit = self._non429 >= self._attempts
        else:
            self._rate_limited += 1
            cap_hit = self._rate_limited >= self._rate_limit_attempts
        if is_429:
            wait = retry_after if retry_after is not None \
                else min(2 ** (self._total - 1) * 2, 30.0) + random.uniform(0, 2)
        else:
            wait = self._backoff_mult * self._total + random.uniform(0, 1)
        remaining = self._deadline - time.monotonic()
        # 上一次 attempt 已是边界（超时==min 的最小有效窗）仍失败 → 连最小有效窗都用掉了，之后只会
        # 发 sub-min 死亡窗或对「即时失败」0-sleep 空转 → 直接判 exhausted。即时失败几乎不消耗
        # timeout 时间，仅靠 remaining 判据会在窗口边沿永远 ≥ window 触发不了 → 空转到次数上限；
        # 此守卫把「边界 attempt 失败」定为终态，确定性停在最后一次有效窗（D1b 紧界回归不闪断）。
        last_was_boundary = self._last_to is not None and self._last_to <= _ATTEMPT_MIN_S + 1e-9
        if last_was_boundary or remaining < _ATTEMPT_WINDOW_S:
            cap_hit = True
        else:
            max_wait = remaining - _ATTEMPT_WINDOW_S  # 睡满 max_wait 后仍留 margin+min 的有效窗
            if wait > max_wait:
                wait = max_wait
            # sleep 前用算术定死下一次超时：sleep wait 后剩余 ≈ remaining−wait，其有效窗剩
            # remaining−wait−margin ≥ min（因 remaining−wait ≥ window）→ 超时不受过冲影响。
            self._next_to = min(self._ceiling, remaining - wait - _ATTEMPT_TIMEOUT_MARGIN_S)
        if cap_hit:
            if is_429:
                print(f"{self._tag}Rate limited (429), all {self._total} attempts exhausted")
                raise RuntimeError(f"{self._err} rate limited (429) after {self._total} attempts: {exc}")
            print(f"{self._tag}All {self._total} attempts failed: {exc}")
            raise RuntimeError(f"{self._err} failed after {self._total} attempts: {exc}")
        if is_429:
            print(f"{self._tag}Rate limited (429), attempt {self._total}, waiting {wait:.1f}s")
        else:
            print(f"{self._tag}Attempt {self._total} failed: {exc}, retrying in {wait:.1f}s...")
        return wait


class ToolsNotSupportedError(RuntimeError):
    """Provider 不支持 tools 参数时抛出，上层据此降级到 legacy 路径。"""


def _infer_finalize(sp, self, result, exc) -> None:
    """推理 span 收尾：补 model + last_usage（OTEL 关时 sp=None，直接返回）。"""
    if sp is None:
        return
    T.set_attr(sp, "model", self._model)
    if exc is None:
        usage = getattr(self, "last_usage", None)
        if usage:
            T.set_usage(sp, usage.get("prompt_tokens"), usage.get("completion_tokens"))


def _async_infer_finalize(sp, self, result, exc) -> None:
    """async_chat span 收尾：usage 取返回的 (text, usage) 元组（async_chat 不写 self.last_usage）。"""
    if sp is None:
        return
    T.set_attr(sp, "model", self._model)
    if exc is None and isinstance(result, tuple) and len(result) == 2 and result[1]:
        usage = result[1]
        T.set_usage(sp, usage.get("prompt_tokens"), usage.get("completion_tokens"))


class LLMAdapter:
    """封装 DeepSeek Chat API 调用。

    支持两种初始化方式：
    1. 显式传参：``LLMAdapter(api_key=..., base_url=..., model=...)`` — 用户配置
    2. 配置文件：``LLMAdapter(config_path=...)`` — 管理员 fallback
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        root = Path(__file__).resolve().parent.parent
        load_dotenv(root / ".env")

        # Load defaults from config.yaml (fallback to config.example.yaml)
        cfg_file = Path(config_path) if config_path is not None else root / "config.yaml"
        if config_path is None and not cfg_file.exists():
            cfg_file = root / "config.example.yaml"
        llm_cfg: dict[str, Any] = {}
        try:
            raw = cfg_file.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if isinstance(data, dict) and "llm" in data:
                llm_cfg = data["llm"]
        except Exception as exc:
            print(f"[LLMAdapter] Config file load failed, using defaults: {exc}")
            pass

        self._base_url = base_url or str(llm_cfg.get("base_url", "https://api.deepseek.com"))
        self._model = model or str(llm_cfg.get("model", "deepseek-v4-pro"))
        self._temperature = temperature if temperature is not None else float(llm_cfg.get("temperature", 0.7))
        self._max_tokens = max_tokens if max_tokens is not None else int(llm_cfg.get("max_tokens", 4096))
        self._presence_penalty = float(llm_cfg.get("presence_penalty", 0.3))
        self.last_usage: dict | None = None

        resolved_key = api_key or llm_cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY")
        self._api_key = resolved_key

        if not resolved_key:
            raise RuntimeError("missing API key — configure in Settings or set DEEPSEEK_API_KEY")

        try:
            # max_retries=0：撤掉 SDK 层静默重试（否则外层 budget × SDK retry=2 = 至多 9 次 HTTP，
            # 阶段 D 缺陷 #1）。所有重试由方法内 _RetryBudget 单点控制。
            self._client = OpenAI(api_key=resolved_key, base_url=self._base_url,
                                  timeout=600.0, max_retries=0)
            self._async_client = AsyncOpenAI(api_key=resolved_key, base_url=self._base_url,
                                             timeout=600.0, max_retries=0)
        except Exception as exc:
            print(f"初始化 OpenAI 客户端失败：{exc}")
            raise

    def _make_async_client(self) -> AsyncOpenAI:
        """Create a standalone AsyncOpenAI for a single asyncio.run cycle."""
        return AsyncOpenAI(api_key=self._api_key, base_url=self._base_url,
                           timeout=600.0, max_retries=0)

    async def aclose(self) -> None:
        """幂等关闭异步客户端。"""
        client = getattr(self, "_async_client", None)
        if client is None:
            return
        self._async_client = None
        try:
            await client.close()
        except Exception:
            pass

    @property
    def model(self) -> str:
        return self._model

    def _build_messages(self, system_prompt: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """组装包含系统提示的对话消息列表。"""
        return [{"role": "system", "content": system_prompt}, *messages]

    async def achat(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str:
        """异步非流式对话，返回完整文本回复。最多重试3次。"""
        result, usage = await self.async_chat(system_prompt, messages, max_tokens=max_tokens)
        if usage:
            self.last_usage = usage
        return result

    @T.spanned("llm.chat", op="chat", finalize=_infer_finalize)
    def chat(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str:
        """非流式对话，返回完整文本回复。重试预算=生成轮（3 次非429 / 总墙钟 60s，_RetryBudget）。"""
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        budget = _RetryBudget(attempts=_GEN_ATTEMPTS, deadline_s=_GEN_DEADLINE_S,
                              ceiling_s=_GEN_ATTEMPT_S, backoff_mult_s=_GEN_BACKOFF_S,
                              err_prefix="LLM API")
        while True:
            timeout = budget.attempt_timeout()
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    temperature=self._temperature,
                    max_tokens=_mt,
                    presence_penalty=self._presence_penalty,
                    timeout=timeout,
                    extra_body={"enable_thinking": False},
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                content = choices[0].message.content or ""
                if completion.usage:
                    self.last_usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
                return content
            except Exception as exc:
                time.sleep(budget.on_failure(exc))

    @T.async_spanned("llm.chat", op="chat", finalize=_async_infer_finalize)
    async def async_chat(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None, client: AsyncOpenAI | None = None) -> tuple[str, dict | None]:
        """异步非流式对话，用于 Map 阶段并发。最多重试3次（非429）或5次（429限流）。

        Args:
            client: 可选的自定义 AsyncOpenAI，用于 per-asyncio-run 场景；
                    不传时使用 self._async_client（默认共享实例）。

        Returns ``(result, usage)`` where *usage* is ``{"prompt_tokens": N,
        "completion_tokens": N}`` or *None*.  Callers are responsible for
        aggregating usage across concurrent calls instead of relying on the
        shared ``last_usage`` attribute.  """
        _c = client or self._async_client
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        budget = _RetryBudget(attempts=_GEN_ATTEMPTS, deadline_s=_GEN_DEADLINE_S,
                              ceiling_s=_GEN_ATTEMPT_S, backoff_mult_s=_GEN_BACKOFF_S,
                              log_prefix="LLMAdapter async", err_prefix="Async LLM")
        while True:
            timeout = budget.attempt_timeout()
            try:
                completion = await _c.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    temperature=self._temperature,
                    max_tokens=_mt,
                    presence_penalty=self._presence_penalty,
                    timeout=timeout,
                    extra_body={"enable_thinking": False},
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                result = choices[0].message.content or ""
                usage = None
                if completion.usage:
                    usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
                return result, usage
            except Exception as exc:
                await asyncio.sleep(budget.on_failure(exc))

    @T.spanned("llm.chat_stream", op="chat", finalize=_infer_finalize)
    def chat_stream(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> Generator[str, None, None]:
        """流式对话，按增量产出文本片段。"""
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        prompt_chars = sum(len(m.get("content", "")) for m in payload)
        self.last_usage = None  # 切断上一轮污染
        # SDK max_retries 已归 0：create()（吐首 chunk 前）连接失败不再被 SDK 静默重试，
        # 在此用同一 _RetryBudget 做有界补偿（≤_STREAM_ATTEMPTS / ≤_STREAM_DEADLINE_S /
        # 退避 1s），429 也走 _classify_retry 的 Retry-After——不再是手写第四份循环。
        # 流一旦吐出 chunk 即不可安全重放，故只包 create() 返回前；续流中断仍直接上抛。
        budget = _RetryBudget(attempts=_STREAM_ATTEMPTS, deadline_s=_STREAM_DEADLINE_S,
                              ceiling_s=_STREAM_ATTEMPT_S, backoff_mult_s=_STREAM_BACKOFF_S,
                              log_prefix="LLMAdapter chat_stream")
        while True:
            timeout = budget.attempt_timeout()
            try:
                stream = self._client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    temperature=self._temperature,
                    max_tokens=_mt,
                    presence_penalty=self._presence_penalty,
                    timeout=timeout,
                    stream=True,
                    stream_options={"include_usage": True},
                    extra_body={"enable_thinking": False},
                )
                break
            except Exception as exc:
                time.sleep(budget.on_failure(exc))
        completion_chars = 0
        try:
            for chunk in stream:
                if chunk.usage:
                    self.last_usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "estimated": False,
                    }
                    continue
                choices = chunk.choices
                if not choices:
                    continue
                delta = choices[0].delta
                piece = delta.content
                if piece:
                    completion_chars += len(piece)
                    yield piece
            # 厂商全程未回 usage chunk → 字符估算兜底
            if self.last_usage is None:
                self.last_usage = {
                    "prompt_tokens": int(prompt_chars / 1.5),
                    "completion_tokens": int(completion_chars / 1.5),
                    "estimated": True,
                }
                print(f"[llm] usage chunk missing, estimated from chars (pt~{self.last_usage['prompt_tokens']} ct~{self.last_usage['completion_tokens']})")
        except Exception as exc:
            print(f"读取流式响应失败：{exc}")
            raise

    @T.spanned("llm.chat_with_tools", op="chat", finalize=_infer_finalize)
    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> Any:
        """非流式 function-calling 对话，返回完整 message 对象（含 tool_calls）。

        重试预算=决策轮（2 次非429 / 总墙钟 6s / 退避 1s，_RetryBudget）——决策是路由，
        失败应快速降级（agent_loop 捕获后走 legacy 纯生成），不再烧 5s+10s 的旧退避墙；
        provider 不支持 tools（400 + tool/function 关键词）→ ToolsNotSupportedError，不重试；
        其他 400 → 原样抛出，不重试。
        """
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        budget = _RetryBudget(attempts=_DECISION_ATTEMPTS, deadline_s=_DECISION_DEADLINE_S,
                              ceiling_s=_DECISION_ATTEMPT_S, backoff_mult_s=_DECISION_BACKOFF_S,
                              log_prefix="LLMAdapter chat_with_tools",
                              err_prefix="chat_with_tools")
        while True:
            timeout = budget.attempt_timeout()
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    tools=tools,
                    temperature=self._temperature,
                    max_tokens=_mt,
                    presence_penalty=self._presence_penalty,
                    timeout=timeout,
                    extra_body={"enable_thinking": False},
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                msg = choices[0].message
                if completion.usage:
                    self.last_usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
                return msg
            except BadRequestError as exc:
                err_text = " ".join(
                    filter(None, [exc.message, str(exc), str(getattr(exc, "body", "") or "")])
                ).lower()
                if "tool" in err_text or "function" in err_text:
                    raise ToolsNotSupportedError(
                        f"Provider does not support tools/function-calling: {exc}"
                    ) from exc
                raise  # 其他 400：不重试，原样抛出
            except Exception as exc:
                time.sleep(budget.on_failure(exc))
