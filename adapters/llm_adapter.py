"""基于 OpenAI SDK 的 DeepSeek 兼容接口封装。"""

from __future__ import annotations

import logging
import asyncio
import random
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import os

import httpx2
import openai
import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI, BadRequestError, OpenAI, Timeout

from core import telemetry as T  # OTel 埋点（OTEL_ENABLED 关时装饰器原样返回，零开销）
from core.utils import estimate_usage_from_chars  # 字符→token 估算的唯一出口

logger = logging.getLogger(__name__)


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


def _env_timeout_s(name: str, default_s: float, floor_s: float) -> float:
    """超时类常量的统一 env 出口：缺变量取默认值，floor 兜住 0/负把超时静默关掉。

    填 0 → create(timeout=0) 或 deadline=0 会静默撤掉超时封顶，比配个偏小的值更危险，故夹 floor。
    """
    return max(float(os.getenv(name, str(default_s))), floor_s)


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
_DECISION_BACKOFF_S = 1.0
_GEN_ATTEMPTS = 3
_GEN_BACKOFF_S = 5.0
# 流式首 token 补偿：SDK 重试撤除后 create()（吐 chunk 前）连接失败不再被 SDK 静默重试，
# 折叠到同一 _RetryBudget（≤_STREAM_ATTEMPTS / ≤_STREAM_DEADLINE_S / 退避 1s）。
# 一旦已 yield 内容即不可安全重放，只包 create() 返回前。
_STREAM_ATTEMPTS = 2
_STREAM_BACKOFF_S = 1.0
# 429 独立次数上限（旧 rate_limit_budget=5 语义，D1a 审计必修2）：429 不计入非429 attempts，
# 但需自身上限，否则 Retry-After 极小(如 0.1)时决策轮 6s 内可高频重打数十次 → 加速 provider
# 侧用户 key 封禁。两个计数器、两个上限、共享一个 deadline。
_RATE_LIMIT_ATTEMPTS = 5
# D1b per-attempt timeout 组：
#   _*_ATTEMPT_S           role ceiling（单次 create 超时上限），决策 5 / 生成 45 / 流式 7。
#   _*_DEADLINE_S          budget 起算的总墙钟上限，决策 6 / 生成 60 / 流式 8。
#   六者同走 _env_timeout_s 出口（LLM_DECISION_ATTEMPT_S / LLM_GEN_ATTEMPT_S /
#   LLM_STREAM_ATTEMPT_S / LLM_DECISION_DEADLINE_S / LLM_GEN_DEADLINE_S /
#   LLM_STREAM_DEADLINE_S，默认值不变，同 CARD_GUARD_ENABLED 模式）——生产发现太紧
#   改环境变量即可，不发版。缺陷 8：此前只 env 化了三个 ceiling，deadline 漏网 →
#   「超时可调」名不副实（生成轮 60s 墙钟被烧死在代码里）。
#   _ATTEMPT_TIMEOUT_MARGIN_S  超时触发(on_failure 裁决)须落在 deadline 内的收尾余量
#   _ATTEMPT_MIN_S             一次有效 attempt 的最小超时；剩余连 margin+min 都撑不起则拒发
_ATTEMPT_TIMEOUT_MARGIN_S = 1.0
_ATTEMPT_MIN_S = 0.25
_ATTEMPT_WINDOW_S = _ATTEMPT_TIMEOUT_MARGIN_S + _ATTEMPT_MIN_S  # 撑起一次 attempt 所需剩余 = 1.25s
# floor：ceiling 取 _ATTEMPT_MIN_S（防 create(timeout=0) 变 no-timeout，静默撤掉单次封顶）；
# deadline 取 _ATTEMPT_WINDOW_S（deadline 撑不起一次有效窗 = 静默关掉全部 attempt）。
_DECISION_ATTEMPT_S = _env_timeout_s("LLM_DECISION_ATTEMPT_S", 5.0, _ATTEMPT_MIN_S)
_GEN_ATTEMPT_S = _env_timeout_s("LLM_GEN_ATTEMPT_S", 45.0, _ATTEMPT_MIN_S)
_STREAM_ATTEMPT_S = _env_timeout_s("LLM_STREAM_ATTEMPT_S", 7.0, _ATTEMPT_MIN_S)
# 长输出（角色卡 / 合并 / 格式化）首字节前的静默可到分钟级：DeepSeek 在排队等调度时才发
# keep-alive，开始推理后读长输入（prefill）期间完全无数据。流式的 `_STREAM_ATTEMPT_S`
# 是「首 token 补偿」口径（7s）且以**标量**传给 httpx —— 标量会同时设成读超时，即
# 「两个数据块之间最多等 7s」，长输入一 prefill 就假失败（生产识别 500 的根因）。
# 300s 的界：上游对未开始推理的请求 10 分钟关连接、nginx 读超时 600s，300 在两者之内
# 且远大于任何合理 prefill。**不做 env 出口**（红线：不新增配置项）—— 它是结构性上限，
# 不是调优旋钮；真要可调，把它改成 `_env_timeout_s(...)` 一行即可。
_BATCH_STREAM_READ_S = 300.0
_DECISION_DEADLINE_S = _env_timeout_s("LLM_DECISION_DEADLINE_S", 6.0, _ATTEMPT_WINDOW_S)
_GEN_DEADLINE_S = _env_timeout_s("LLM_GEN_DEADLINE_S", 60.0, _ATTEMPT_WINDOW_S)
_STREAM_DEADLINE_S = _env_timeout_s("LLM_STREAM_DEADLINE_S", 8.0, _ATTEMPT_WINDOW_S)


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
            # 只是换抛出类型/带上屏文案：f-string 与现在逐字相同，core 侧既有的
            # "failed after N attempts" / "rate limited (429)" 判据继续命中。
            user_message = _upstream_user_message(exc)
            if is_429:
                # 所有 LLM 失败的必经点：耗尽后必须落到 logger（print 只进容器 stdout，
                # GlitchTip 与告警都看不见）。模板用 %s 占位，GlitchTip 才按同一模板归并。
                logger.error("%sRate limited (429), all %s attempts exhausted", self._tag, self._total)
                raise UpstreamFailure(
                    f"{self._err} rate limited (429) after {self._total} attempts: {exc}",
                    user_message=user_message)
            logger.error("%sAll %s attempts failed: %s: %s",
                         self._tag, self._total, type(exc).__name__, exc)
            raise UpstreamFailure(
                f"{self._err} failed after {self._total} attempts: {exc}",
                user_message=user_message)
        if is_429:
            logger.warning(
                "%sRate limited (429), attempt %s, waiting %.1fs",
                self._tag, self._total, wait,
            )
        else:
            logger.warning(
                "%sAttempt %s failed: %s, retrying in %.1fs...",
                self._tag, self._total, exc, wait,
            )
        return wait


class ToolsNotSupportedError(RuntimeError):
    """Provider 不支持 tools 参数时抛出，上层据此降级到 legacy 路径。"""


# ── 调用点门：守卫钩子 + 拒绝异常（spec §2.5）──────────────────────────
# 「谁在调」是事实、「许不许调」是策略：本层只定义**钩子契约**（拿 base_url 换一个
# 拒绝理由），策略由 web 在启动时向下注册实现。未注册 = 不做检查，独立进程
# （mcp_server / scripts）保持现状，不被迫装配一个门。
_call_guard: Callable[[str], str | None] | None = None


def set_call_guard(fn: Callable[[str], str | None] | None) -> None:
    """注册出站前守卫。契约：``fn(base_url) -> str | None``，只返回拒绝理由（不抛）。"""
    global _call_guard
    _call_guard = fn


def get_call_guard() -> Callable[[str], str | None] | None:
    """与 ``set_call_guard`` 配对的读口（同 ``get_loop_submitter`` 的形状）。"""
    return _call_guard


class OutboundRefused(RuntimeError):
    """出站**之前**被门拦下 —— 请求根本没发出去。

    门有两种拦法：判定给出理由（`LLMCallRefused`）、上下文里没有身份（`web.llm_gate`
    的 `LLMCallerMissing`，fail-closed）。两者的共同点才是调用方要判的：「这不是一次
    失败的请求，是一次没发生的请求」。故在这层给一个共同基类，**唯一**的作用就是让
    下游（如 `core/embeddings.py` 的批处理）能用一句 `except` 把两者一并放行 ——
    否则得在 core 里 import `web`，撞 L13。
    """


class LLMCallRefused(OutboundRefused):
    """调用点门拒绝：守卫给出理由 → 出站**之前**抛，请求根本没发出去。

    ``reason`` 是守卫给的、已审的上屏口径（geo 那条就是 geo_guard 的文案），
    故它同时是 ``user_message``；``base_url`` 留着给审计（哪家的调用被挡了）。
    """

    def __init__(self, reason: str, base_url: str) -> None:
        self.reason = reason
        self.base_url = base_url
        super().__init__(f"{base_url}: {reason}")

    def __reduce__(self):
        """跨进程重建（缺陷 18）：``args`` 是格式化后的 message，与
        ``__init__(reason, base_url)`` 对不上，显式还原两个字段。"""
        return (self.__class__, (self.reason, self.base_url))

    @property
    def user_message(self) -> str:
        return self.reason


def check_outbound_guard(base_url: str) -> None:
    """出站前守卫的**唯一**实现：守卫给理由就抛，没注册守卫就放行。

    `LLMAdapter._before_call` 与嵌入出站（`core/embeddings.py` 的 `_call_api`）共用
    这一处 —— 两处各存一份判定，改一处漏一处时两边的放行口径会悄悄分叉。读的是模块级
    全局、每次现读，故测试临时换守卫、生产启动时注册都不必重建调用方实例。
    """
    guard = _call_guard
    if guard is None:
        return
    reason = guard(base_url)
    if reason:
        raise LLMCallRefused(reason, base_url)


def _infer_finalize(sp, self, result, exc) -> None:
    """推理 span 收尾：补 model + usage（OTEL 关时 sp=None，直接返回）。

    usage 的来源按入口分两类：**流式**入口（``chat_stream`` / ``chat_stream_long``）把本次
    用量随返回值交给调用方，收尾这里也取 ``result`` 那一份 —— 与记账侧同源，顺带不再读
    共享槽；其余入口（``chat`` 返回正文、``chat_with_tools`` 返回 message 对象）仍只有
    共享属性可读。
    """
    if sp is None:
        return
    T.set_attr(sp, "model", self._model)
    if exc is None:
        usage = result if isinstance(result, dict) else getattr(self, "last_usage", None)
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


# ── 供应商方言层（唯一请求选项控制点）─────────────────────────────────
# base_url 用户可配（见 __init__），本适配器实际服务多家 OpenAI 兼容端点。同一个意图
# （"关闭思考"）在不同家的 payload 不同，硬编码任一家都会静默打偏：
#   extra_body={"enable_thinking": False} 是 Qwen 方言，DeepSeek 不认、静默忽略 →
#   思考照开、与正文共享 max_tokens → 正文被吃光返回空内容（实测见 AGENTS.md「蒸馏管线」）。
# 故调用点只表达意图，payload 由本表决定；四处调用点不得再各拼 extra_body 字面量。
_DIALECT_DEEPSEEK = "deepseek"
_DIALECT_QWEN = "qwen"
_DIALECT_UNKNOWN = "unknown"

# 方言指纹：base_url / model 的小写子串匹配（顺序即优先级）。
_DIALECT_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (_DIALECT_DEEPSEEK, ("deepseek",)),
    (_DIALECT_QWEN, ("dashscope", "aliyuncs", "qwen")),
)

# 「关闭思考」意图 → 各方言 payload。空 dict = 该供应商无此开关 → 不传 extra_body。
# 未知供应商走空 dict 是安全默认：宁可开着思考（仅多花预算），也不发一个可能被 400
# 拒绝的未知字段（那会让该用户所有调用永久失败）。
_THINKING_DISABLED: dict[str, dict[str, Any]] = {
    _DIALECT_DEEPSEEK: {"thinking": {"type": "disabled"}},
    _DIALECT_QWEN: {"enable_thinking": False},
    _DIALECT_UNKNOWN: {},
}


def _detect_dialect(base_url: str | None, model: str | None) -> str:
    """由 base_url + model 推断供应商方言；认不出返回 _DIALECT_UNKNOWN。

    base_url 优先：它标识「在跟谁说话」，而 model 可能是网关的别名，也可能是
    config 里的默认值（用户只换 base_url 时 model 不会跟着变）。base_url 认不出
    再退回 model 名。两条线索都认不出 → unknown。
    """
    for source in (base_url, model):
        hay = (source or "").lower()
        for dialect, needles in _DIALECT_FINGERPRINTS:
            if any(n in hay for n in needles):
                return dialect
    return _DIALECT_UNKNOWN


# ── 响应校验层（唯一 finish_reason 裁决点）─────────────────────────────
# 缺陷（与重试嵌套/线程弃船/维度不符同根因的第四次显形）：全仓生产代码从不读
# finish_reason → 截断或「被思考吃光」的响应被当成功返回并落库（实测：52 字节半截内容
# 落满 6 片，二次续跑 map 调用 = 0）。四个提取点此前各自沉默：非流式三处 content 直取、
# 流式一处直接 yield delta。本层收敛为单一裁决点，调用点不得再各自判：
#   _INCOMPLETE_FINISH_REASONS → 显式抛 IncompleteResponseError，绝不返回空串/半截内容
#   其余（stop / tool_calls / 真正陌生的值 / 缺失）→ 放行；陌生值与缺失各记一条点名 WARN
# 截断（IncompleteResponseError）、网络故障（RuntimeError/超时…）、空内容（放行但返回
# ""）三者互不混淆——失败必须可辨，不只是可见。
# 两道防线的分工：本层是第一道（上游截断在源头显式失败）；续跑侧的第二道在
# core/distiller.py 的 _resume_hit 第 2 道，是纵深防御——只挡空结果、不承诺结构校验。
# 不要把结构校验的期望挪到那边，也不要指望本层挡住「模型自然 stop 但内容不完整」。
_INCOMPLETE_FINISH_REASONS = frozenset({
    "length",                        # 输出被 max_tokens 截断
    "content_filter",                # 内容被上游安全策略过滤
    "insufficient_system_resource",  # 上游资源不足
})

# 未完成 → 处置建议（面向运维/日志）。三者动作不同，只报「失败」会让上层猜错：
#   length 抬预算 / content_filter 改输入（重试无用）/ insufficient_* 可重试。
_INCOMPLETE_ACTIONS: dict[str, str] = {
    "length": "输出被 max_tokens 截断 —— 抬 llm.max_tokens（或 LLM_MAX_TOKENS）或调小 chunk_size",
    "content_filter": "内容被上游安全策略过滤 —— 需改输入，重试无用",
    "insufficient_system_resource": "上游资源不足 —— 属瞬时故障，可重试",
}

# 未完成 → 面向终端用户的说法。运维口径（「抬 max_tokens」「调小 chunk_size」）不能给
# 聊天用户看；SSE 错误帧用这张表，日志与蒸馏任务错误用上一张。
_INCOMPLETE_USER_MESSAGES: dict[str, str] = {
    "length": "回复被截断，请重试",
    "content_filter": "内容被安全策略拦截，请修改后重试",
    "insufficient_system_resource": "服务繁忙，请稍后重试",
}
_OK_FINISH_REASONS = frozenset({"stop", "tool_calls"})

# 上游已知失败 → 能指导下一步的中文文案（缺陷 94 的泄漏那半）。这三条是可判定的处置：
# key 无效去设置页、余额不足去充值、限流等一会儿 —— 通用文案给不了这些下一步。
# 未登记的状态码不在这里（落 "" → 出口通用文案）：宁可口径笼统，也不把上游报错原文上屏。
_UPSTREAM_USER_MESSAGES: dict[int, str] = {
    401: "API Key 无效或无权限，请到设置页检查",
    403: "API Key 无效或无权限，请到设置页检查",
    402: "账户余额不足，请充值后重试",
    429: "请求过于频繁，请稍后再试",
}


class UpstreamFailure(RuntimeError):
    """上游已知失败重试耗尽 —— 带能指导用户下一步的上屏文案。

    ``user_message`` 走 keyword、有默认值 ""：空串即「未登记的状态码」，出口
    （``user_facing_error``）据它落通用文案。默认值也让 ``cls(*args)`` 仍能重建 ——
    不必自定义 ``__reduce__``（同 ``core.distiller.DistillError`` 先例）。
    """

    def __init__(self, message: str, user_message: str = "") -> None:
        self.user_message = user_message
        super().__init__(message)


# 传输层失败：连接建立之后 / 读流途中的网络故障。**只在这里定义一次** —— 建流阶段的
# ``_RetryBudget.on_failure`` 与读流阶段的 ``_stream`` 判「是不是传输层」都取这一处。
#
# 为什么要两个类：openai 的包装行为会随版本变。3.13.0 把流中途的传输层异常原样抛出
# （httpx2.TransportError 子类），3.19.2 起在 openai/_streaming.py 把它包成
# APIConnectionError（超时是其子类 APITimeoutError），而它**不是** httpx2.TransportError 的
# 子类 —— 只认 httpx2 的话，openai 一升过 3.1x 这里就静默失效（sentinel 上的 500 即此）。
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    httpx2.TransportError,
    openai.APIConnectionError,
)


def _upstream_user_message(exc: Exception) -> str:
    """上游异常 → 上屏文案；未登记的状态码 → ""（由出口落通用文案）。

    传输层失败先判：它没有 status_code，落进状态码表只会得到 ""（通用文案），而这类失败
    的处置是「重发一次」而不是「去设置页检查 key」。文案与 ``_GENERIC_USER_ERROR``（"服务
    暂时不可用…"）刻意不同，措辞更指向动作，也便于据文案分辨「有没有被认成传输层」。
    其余识别口径与 ``_classify_retry`` 的 429 判定一致（先看 ``status_code``，其次报错文本
    里的 429）—— 免得同一次失败在「算不算限流」和「该说什么」两处各判出一个答案。
    """
    if isinstance(exc, _TRANSPORT_ERRORS):
        return "模型服务暂时不可用，请稍后重试"
    code = getattr(exc, "status_code", None)
    if code is None and "429" in str(exc):
        code = 429
    return _UPSTREAM_USER_MESSAGES.get(code, "")


class IncompleteResponseError(RuntimeError):
    """finish_reason 属 _INCOMPLETE_FINISH_REASONS —— 内容不完整，不可当成功落库。

    ``content`` 挂已生成的部分正文。只作属性、不进 message：message 会经路由层
    截首行上屏（web/routers/distill.py），正文混进去等于把半截角色卡给用户看。
    截断自愈环靠它把上游确定信号接回重修，见 core/distiller.py 的 _chat_accounted。
    """

    def __init__(self, finish_reason: str, where: str, content: str = "") -> None:
        self.finish_reason = finish_reason
        self.where = where
        self.content = content
        self.hint = _INCOMPLETE_ACTIONS.get(
            finish_reason, "未登记处置 —— 补 adapters/llm_adapter.py 的 _INCOMPLETE_ACTIONS")
        super().__init__(
            f"{where}: 上游响应不完整（finish_reason={finish_reason!r}）—— {self.hint}；"
            f"已按失败处理、不作为结果返回，已生成部分见 .content。"
        )

    def __reduce__(self):
        """跨进程重建（缺陷 18）：BaseException.__reduce__ 会调 cls(*self.args)，而 args 是
        格式化后的 message、与本类 __init__(finish_reason, where) 参数对不上 —— 反序列化炸成
        TypeError，把真因换成另一个异常。显式还原三个字段（hint 由 finish_reason 重算）。"""
        return (self.__class__, (self.finish_reason, self.where, self.content))

    @property
    def user_message(self) -> str:
        """面向用户的文案：不含 where（日志标识）与运维口径的处置建议。"""
        return _INCOMPLETE_USER_MESSAGES.get(self.finish_reason, "回复未完成，请重试")


def llm_error_payload(exc: BaseException) -> dict[str, Any] | None:
    """错误边界：把 LLM 侧的已知失败翻成上线格式，其余返回 None。

    调用方（路由层）据此拿 code / finish_reason / 上屏文案，**无需 import 异常类**——
    否则每加一个异常类，core/web 就多一处 isinstance 耦合。本层是这条边界的唯一出口。

    **判别键 `kind`**（配码与审计只认它，不认异常类名）：未完成终态是
    ``incomplete:<finish_reason>``、调用点门拒绝是 ``call_refused``、上游失败是
    ``upstream``。同族的几种已知失败因此共用一张表 —— 配码那侧没有「先查 A 表再查
    B 表」。原有的 `code` / `finish_reason` 字段保留给既有调用方。
    """
    if isinstance(exc, LLMCallRefused):
        return {"code": "call_refused", "error": exc.reason, "kind": "call_refused",
                "base_url": exc.base_url, "finish_reason": ""}
    if isinstance(exc, IncompleteResponseError):
        return {"code": "incomplete_response", "error": exc.user_message,
                "finish_reason": exc.finish_reason,
                "kind": f"incomplete:{exc.finish_reason}"}
    if isinstance(exc, UpstreamFailure):
        # `user_message` 可能为 ""（未登记的状态码 / 裸的传输层故障）：出口
        # ``user_facing_error`` 把 payload["error"] 原样上屏，空串会显示成一片空白
        # 而不是「服务不可用」—— 所以这里就落通用文案，别把空串漏到出口。
        return {"code": "upstream", "error": exc.user_message or _GENERIC_USER_ERROR,
                "finish_reason": "", "kind": "upstream"}
    return None


def llm_error_types() -> tuple[type[BaseException], ...]:
    """本层已知失败的异常类，供装配层注册 ``add_exception_handler``。

    与 ``llm_error_payload`` 同一取向：**装配层不 import 异常类**（边界锁
    ``tests/test_chat_stream_error.py::test_no_exception_class_leaks_into_core_web_storage``
    禁 core/web/storage 出现该标识）。新增一种 LLM 失败 = 在这里的元组加一个类，
    装配层零改动。"""
    return (IncompleteResponseError, LLMCallRefused, UpstreamFailure)


_GENERIC_USER_ERROR = "服务暂时不可用，请稍后重试"


def user_facing_error(exc: BaseException) -> str:
    """异常 → 用户可见文案的**唯一出口**。内部标识一律不上屏。

    取值优先级（先具体后笼统）：
      1. ``llm_error_payload`` —— LLM 侧已知失败，取 ``_INCOMPLETE_USER_MESSAGES`` 上屏表
      2. ``exc.user_message`` —— 自带已审上屏口径的异常（``core.distiller.DistillError``、
         ``adapters.llm_adapter.UpstreamFailure``）
      3. 通用文案

    **绝不使用 ``type(exc).__name__``**：异常类名是内部标识（缺陷 17 的旧实现在
    兜底分支上打了它）。也绝不把运维口径（finish_reason / max_tokens / 分片计数 /
    ``where``）带上屏——那些留在异常自己的 ``str()`` 里进日志。

    未登记的异常**一律**落通用文案：这里曾经有个可选开关，给 chat 的 SSE 帧原样透出
    ``str(exc)`` 以便排障。但那条路把上游报错原文直接送到了用户面前（缺陷 94 的泄漏
    那半），开关已删 —— 排障靠日志里那句原文，不靠上屏。
    """
    payload = llm_error_payload(exc)
    if payload is not None:
        return payload["error"]
    declared = getattr(exc, "user_message", None)
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    return _GENERIC_USER_ERROR


def incomplete_response_info(exc: BaseException) -> tuple[str, str] | None:
    """未完成终态 → ``(finish_reason, 已生成的部分正文)``；其余异常 → ``None``。

    与 ``llm_error_payload`` 并列的第二条边界出口：core 侧的截断自愈环据此拿到
    上游确定信号，而**无需 import 异常类**——否则每加一个异常类，core/web 就多
    一处 isinstance 耦合（边界锁见 tests/test_chat_stream_error.py）。
    """
    if isinstance(exc, IncompleteResponseError):
        return exc.finish_reason, getattr(exc, "content", "")
    return None


def _check_finish_reason(finish_reason: str | None, *, where: str, content: str = "") -> None:
    """已知未完成终态 → 抛；其余放行。陌生值与缺失点名 WARN（不同供应商语义不一，不阻断）。"""
    if finish_reason in _INCOMPLETE_FINISH_REASONS:
        raise IncompleteResponseError(finish_reason, where, content=content)
    if finish_reason in _OK_FINISH_REASONS:
        return
    print(f"[llm] WARNING: {where}: 未识别的 finish_reason={finish_reason!r} —— 按正常处理；"
          f"若确属截断，加入 adapters/llm_adapter.py 的 _INCOMPLETE_FINISH_REASONS")


def _checked_message(choice: Any, *, where: str) -> Any:
    """校验后返回 message 对象（chat_with_tools 需要 tool_calls，故不在此取 content）。"""
    _check_finish_reason(getattr(choice, "finish_reason", None), where=where)
    return choice.message


def _extract_content(choice: Any, *, where: str) -> str:
    """校验后取正文；空 content 仍是空串（那是「无内容」，≠ 截断）。

    先取后校验：截断时把已生成的部分正文挂到异常上，供 core 侧截断自愈环重修。
    """
    msg = getattr(choice, "message", None)
    content = getattr(msg, "content", None) or ""
    _check_finish_reason(getattr(choice, "finish_reason", None), where=where, content=content)
    return content


def _resolve_max_tokens(llm_cfg: dict[str, Any]) -> int:
    """max_tokens 取值阶梯：显式 arg > LLM_MAX_TOKENS > config.yaml > 4096。

    env 必须压过 config.yaml，否则这个出口是死的（镜像里总有 config.yaml）。与三个
    ceiling（LLM_DECISION_ATTEMPT_S / LLM_GEN_ATTEMPT_S / LLM_STREAM_ATTEMPT_S）同模式：
    生产发现值不对，改环境变量即可，不发版。现值 4096 无量化依据（来历见 config 注释）。
    """
    env = os.getenv("LLM_MAX_TOKENS")
    if env and env.strip():
        try:
            return int(env)
        except ValueError:
            logger.warning("[LLMAdapter] LLM_MAX_TOKENS=%r 不是整数，回退 config.yaml", env)
    return int(llm_cfg.get("max_tokens", 4096))


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
            logger.error("[LLMAdapter] Config file load failed, using defaults: %s", exc, exc_info=True)
            pass

        self._base_url = base_url or str(llm_cfg.get("base_url", "https://api.deepseek.com"))
        self._model = model or str(llm_cfg.get("model", "deepseek-v4-pro"))
        self._temperature = temperature if temperature is not None else float(llm_cfg.get("temperature", 0.7))
        self._max_tokens = max_tokens if max_tokens is not None else _resolve_max_tokens(llm_cfg)
        self._presence_penalty = float(llm_cfg.get("presence_penalty", 0.3))
        self._dialect = _detect_dialect(self._base_url, self._model)
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

    @property
    def base_url(self) -> str:
        """本实例实际在跟谁说话 —— 守卫从适配器**自身**读这个事实，不从调用方拿。"""
        return self._base_url

    def _before_call(self) -> None:
        """出站前的唯一检查点：转调 ``check_outbound_guard``。

        所有出站方法都在出站**之前**调它（流式方法在建流之前）。实现只有那一处 ——
        嵌入出站走的是同一个函数，两边的放行口径不会分叉。
        """
        check_outbound_guard(self._base_url)

    def preflight(self) -> None:
        """与 ``_before_call()`` **同一实现**，公开给解析出口（§2.8）用。

        只转调，不重写：两处若各存一份判定，改一处漏一处时两边的放行口径会悄悄分叉。
        """
        self._before_call()

    def _build_messages(self, system_prompt: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """组装包含系统提示的对话消息列表。"""
        return [{"role": "system", "content": system_prompt}, *messages]

    def _request_options(self) -> dict[str, Any]:
        """本次调用的 provider 专属 extra_body —— 唯一控制点。

        调用点只表达「关闭思考」这一意图，方言 payload 由 _THINKING_DISABLED 决定。
        加第三个供应商只改那张表，不碰任何调用点。返回副本，防调用方改坏共享表。
        """
        return dict(_THINKING_DISABLED[self._dialect])

    async def achat(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str:
        """异步非流式对话，返回完整文本回复。最多重试3次。

        调用方（群聊等）靠 ``last_usage`` 记账，故**无条件**回写：厂商未回 usage 时
        置 ``None``（显式「无数据」），不能保留上一次的值 —— 否则记账方会把上一轮
        的 token 当成这一轮的，比不记更糟（错数据冒充真实值）。
        """
        result, usage = await self.async_chat(system_prompt, messages, max_tokens=max_tokens)
        self.last_usage = usage
        return result

    @T.spanned("llm.chat", op="chat", finalize=_infer_finalize)
    def chat(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str:
        """非流式对话，返回完整文本回复。重试预算=生成轮（3 次非429 / 总墙钟 60s，_RetryBudget）。"""
        self._before_call()
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        budget = _RetryBudget(attempts=_GEN_ATTEMPTS, deadline_s=_GEN_DEADLINE_S,
                              ceiling_s=_GEN_ATTEMPT_S, backoff_mult_s=_GEN_BACKOFF_S,
                              err_prefix="LLM API")
        self.last_usage = None  # 切断上一轮污染：本轮无 usage 时不能冒充真实值
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
                    extra_body=self._request_options(),
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                content = _extract_content(choices[0], where="chat")
                if completion.usage:
                    self.last_usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
                return content
            except IncompleteResponseError:
                raise  # 截断是确定性失败：不烧重试预算（同 ToolsNotSupportedError 形状）
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
        self._before_call()   # `achat` 委托到这里，故它不必再调一次
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
                    extra_body=self._request_options(),
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                result = _extract_content(choices[0], where="async_chat")
                usage = None
                if completion.usage:
                    usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
                return result, usage
            except IncompleteResponseError:
                raise  # 截断：确定性失败，不烧重试预算
            except Exception as exc:
                await asyncio.sleep(budget.on_failure(exc))

    def _stream(self, system_prompt: str, messages: list[dict[str, Any]],
                max_tokens: int | None = None, *,
                read_s: float | None = None) -> Generator[str, None, dict | None]:
        """流式的唯一实现，两个公开入口共用（``chat_stream`` / ``chat_stream_long``）。

        ``read_s`` 是**读**阶段的超时上限：``None`` = 取本次 attempt 的 ceiling，即以标量
        交给 httpx、每个阶段（含 read）都用它 —— 交互流逐字维持原行为；给出数值则只放宽
        read，connect / write / pool 仍取 ceiling。

        两个入口的差别只有 ``read_s`` 一个值，故「这类调用读超时多长」只在一处定义：
        调用点不需要（也不该）知道有这回事。

        **本次用量随返回值交出**（PEP 380：生成器 ``return v`` → ``StopIteration.value``），
        记账方不必去读 ``last_usage`` 那个共享槽。共享槽里那个数是**跨调用**的：本函数在
        写下它之后还要继续 ``yield``，控制权交出去的那一刻起就可能被另一条流的写覆盖，
        收尾再读会读到别人的账（缺陷 20 的 300/300 串号）。返回值这一份从写出到返回
        不经过任何共享状态。``last_usage`` 照写，单线程调用方不受影响。
        """
        self._before_call()   # 建流之前：拒绝时连 create() 都不发
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        prompt_chars = sum(len(m.get("content", "")) for m in payload)
        self.last_usage = None  # 切断上一轮污染
        usage: dict | None = None  # 本次用量：随返回值交付，不靠收尾时回头读共享槽
        # SDK max_retries 已归 0：create()（吐首 chunk 前）连接失败不再被 SDK 静默重试，
        # 在此用同一 _RetryBudget 做有界补偿（≤_STREAM_ATTEMPTS / ≤_STREAM_DEADLINE_S /
        # 退避 1s），429 也走 _classify_retry 的 Retry-After——不再是手写第四份循环。
        # 流一旦吐出 chunk 即不可安全重放，故只包 create() 返回前；续流中断仍直接上抛。
        budget = _RetryBudget(attempts=_STREAM_ATTEMPTS, deadline_s=_STREAM_DEADLINE_S,
                              ceiling_s=_STREAM_ATTEMPT_S, backoff_mult_s=_STREAM_BACKOFF_S,
                              log_prefix="LLMAdapter chat_stream")
        while True:
            timeout: float | Timeout = budget.attempt_timeout()
            if read_s is not None:
                # 分项超时：标量会被 httpx 铺到每个阶段（含 read），长 prefill 必超时。
                timeout = Timeout(timeout, read=read_s)
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
                    extra_body=self._request_options(),
                )
                break
            except Exception as exc:
                time.sleep(budget.on_failure(exc))
        completion_chars = 0
        saw_finish_reason = False  # 流式终态只在最后一个 chunk 上出现
        try:
            for chunk in stream:
                if chunk.usage:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "estimated": False,
                    }
                    self.last_usage = usage
                    continue
                choices = chunk.choices
                if not choices:
                    continue
                # 先校验再吐本 chunk：finish_reason=length 时最后一片也不交付。
                # 中间 chunk 恒为 None，故 None 不在此处告警，留到流尽统一判缺失。
                fr = getattr(choices[0], "finish_reason", None)
                if fr is not None:
                    saw_finish_reason = True
                    _check_finish_reason(fr, where="chat_stream")
                delta = choices[0].delta
                piece = delta.content
                if piece:
                    completion_chars += len(piece)
                    yield piece
            if not saw_finish_reason:
                _check_finish_reason(None, where="chat_stream")  # 流尽仍无终态 → 记缺失
            # 厂商全程未回 usage chunk → 字符估算兜底（估算口径的唯一出口在 core.utils，
            # Map 失败分片走的是同一个函数，改系数不会漏一边）
            if usage is None:
                usage = estimate_usage_from_chars(prompt_chars, completion_chars)
                self.last_usage = usage
                print(f"[llm] usage chunk missing, estimated from chars (pt~{usage['prompt_tokens']} ct~{usage['completion_tokens']})")
            return usage
        except IncompleteResponseError:
            raise  # 截断是确定性失败：不吞、不打「读取失败」误导日志、不重试
        except _TRANSPORT_ERRORS as exc:
            # 读流途中的传输层故障（断连 → RemoteProtocolError、读超时 → ReadTimeout，
            # openai ≥3.19 还可能是它自己包出来的 APIConnectionError）：归成上游失败，
            # 交统一出口回 503 + 上屏文案。分类与文案都由 _upstream_user_message 裁决，
            # 与建流阶段同一处 —— 同一个故障在两条路上说出同一句话。
            # **只认传输层**：宽成 except Exception 会把我们自己的 bug（AttributeError
            # 之类）报成上游故障，把用户引去「稍后重试」而不是让我们修。
            # 流**不重放** —— 已吐出的片段不可撤回，重放会重复交付。
            print(f"读取流式响应失败（上游传输层）：{exc}")
            raise UpstreamFailure(
                f"chat_stream 读流中断：{exc}",
                user_message=_upstream_user_message(exc),
            ) from exc   # from：原因链保留（排障要看是断连还是读超时）
        except Exception as exc:
            print(f"读取流式响应失败：{exc}")
            raise

    @T.spanned("llm.chat_stream", op="chat", finalize=_infer_finalize)
    def chat_stream(self, system_prompt: str, messages: list[dict[str, Any]],
                    max_tokens: int | None = None) -> Generator[str, None, dict | None]:
        """交互/小输出用：读超时取本次 attempt 的 ceiling（聊天是 7 s）。"""
        return (yield from self._stream(system_prompt, messages, max_tokens))

    @T.spanned("llm.chat_stream", op="chat", finalize=_infer_finalize)
    def chat_stream_long(self, system_prompt: str, messages: list[dict[str, Any]],
                         max_tokens: int | None = None) -> Generator[str, None, dict | None]:
        """长输出流式的**唯一入口**：角色卡 / 合并 / 格式化走这里。

        存在的理由是「守汇合点」——读超时放宽只在这一个函数里发生，调用点不需要
        （也不该）知道有这回事：那种「5 处各写一遍参数」的写法，漏一处就是一条会在
        生产上静默读超时的路，而漏掉的那处从调用点看不出来。
        """
        return (yield from self._stream(system_prompt, messages, max_tokens,
                                        read_s=_BATCH_STREAM_READ_S))

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
        self._before_call()
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        budget = _RetryBudget(attempts=_DECISION_ATTEMPTS, deadline_s=_DECISION_DEADLINE_S,
                              ceiling_s=_DECISION_ATTEMPT_S, backoff_mult_s=_DECISION_BACKOFF_S,
                              log_prefix="LLMAdapter chat_with_tools",
                              err_prefix="chat_with_tools")
        self.last_usage = None  # 切断上一轮污染：本轮无 usage 时不能冒充真实值
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
                    extra_body=self._request_options(),
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                msg = _checked_message(choices[0], where="chat_with_tools")
                if completion.usage:
                    self.last_usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
                return msg
            except IncompleteResponseError:
                raise  # 截断：确定性失败，不烧重试预算（同 ToolsNotSupportedError 形状）
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
