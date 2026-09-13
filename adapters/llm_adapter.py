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


class IncompleteResponseError(RuntimeError):
    """finish_reason 属 _INCOMPLETE_FINISH_REASONS —— 内容不完整，不可当成功落库。

    ``content`` 挂已生成的部分正文。只作属性、不进 message：message 会经路由层
    截首行上屏（web/routers/distill.py），正文混进去等于把半截角色卡给用户看。
    截断自愈环靠它把上游确定信号接回重修，见 core/distiller.py 的 _chat_initial。
    """

    def __init__(self, finish_reason: str, where: str, content: str = "") -> None:
        self.finish_reason = finish_reason
        self.content = content
        self.hint = _INCOMPLETE_ACTIONS.get(
            finish_reason, "未登记处置 —— 补 adapters/llm_adapter.py 的 _INCOMPLETE_ACTIONS")
        super().__init__(
            f"{where}: 上游响应不完整（finish_reason={finish_reason!r}）—— {self.hint}；"
            f"已按失败处理、不作为结果返回，已生成部分见 .content。"
        )

    @property
    def user_message(self) -> str:
        """面向用户的文案：不含 where（日志标识）与运维口径的处置建议。"""
        return _INCOMPLETE_USER_MESSAGES.get(self.finish_reason, "回复未完成，请重试")


def llm_error_payload(exc: BaseException) -> dict[str, Any] | None:
    """错误边界：把 LLM 侧的已知失败翻成上线格式，其余返回 None。

    调用方（路由层）据此拿 code / finish_reason / 上屏文案，**无需 import 异常类**——
    否则每加一个异常类，core/web 就多一处 isinstance 耦合。本层是这条边界的唯一出口。
    """
    if isinstance(exc, IncompleteResponseError):
        return {"code": "incomplete_response", "error": exc.user_message,
                "finish_reason": exc.finish_reason}
    return None


_GENERIC_USER_ERROR = "服务暂时不可用，请稍后重试"


def user_facing_error(exc: BaseException, *, preserve_unknown: bool = False) -> str:
    """异常 → 用户可见文案的**唯一出口**。内部标识一律不上屏。

    取值优先级（先具体后笼统）：
      1. ``llm_error_payload`` —— LLM 侧已知失败，取 ``_INCOMPLETE_USER_MESSAGES`` 上屏表
      2. ``exc.user_message`` —— 自带已审上屏口径的异常（``core.distiller.DistillError``）
      3. ``preserve_unknown`` 为真 → ``str(exc)``；否则通用文案

    **绝不使用 ``type(exc).__name__``**：异常类名是内部标识（缺陷 17 的旧实现在
    兜底分支上打了它）。也绝不把运维口径（finish_reason / max_tokens / 分片计数 /
    ``where``）带上屏——那些留在异常自己的 ``str()`` 里进日志。

    ``preserve_unknown`` 只给 chat 的 SSE 帧用：那条契约显式锁定「未登记的异常保持原样」
    以便排障（tests/test_chat_stream_error.py::test_other_errors_keep_original_shape）。
    **蒸馏路径一律不传** —— 角色卡页上出现 max_tokens 或异常类名就是缺陷 17 本身。
    """
    payload = llm_error_payload(exc)
    if payload is not None:
        return payload["error"]
    declared = getattr(exc, "user_message", None)
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    if preserve_unknown:
        return str(exc)
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
            print(f"[LLMAdapter] LLM_MAX_TOKENS={env!r} 不是整数，回退 config.yaml")
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
            print(f"[LLMAdapter] Config file load failed, using defaults: {exc}")
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
                    self.last_usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "estimated": False,
                    }
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
            # 厂商全程未回 usage chunk → 字符估算兜底
            if self.last_usage is None:
                self.last_usage = {
                    "prompt_tokens": int(prompt_chars / 1.5),
                    "completion_tokens": int(completion_chars / 1.5),
                    "estimated": True,
                }
                print(f"[llm] usage chunk missing, estimated from chars (pt~{self.last_usage['prompt_tokens']} ct~{self.last_usage['completion_tokens']})")
        except IncompleteResponseError:
            raise  # 截断是确定性失败：不吞、不打「读取失败」误导日志、不重试
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
