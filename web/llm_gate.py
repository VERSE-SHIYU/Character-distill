# -*- coding: utf-8 -*-
"""LLM 访问门：**策略**与出口（spec v5 §2.6）。

身份上下文（`Caller` / `SYSTEM` / `LLM_CALLER` / `system_llm_context`）在
`core/request_context.py` —— 本模块 **import 它，不重新定义**。这样分是因为两件事
的职责线不同：「谁在调」是事实，「许不许调」是策略。`web/` 没有 `__init__.py`，
换个模块名再定义一次 `LLM_CALLER` 会得到**第二个** ContextVar，中间件设的值这边
根本读不到 —— 那不是风格问题，是两侧各看各的。（上下文住 `core/` 而不是 `web/`：
记账出口 `core/distiller.py` 也读它，而 `core` 不许 import `web`，见 L13。）

门位是**调用点门**：判定结果以「拒绝理由」的形式经 `LLMCallRefused` 抛在出站之前，
调用方不需要知道 gate 模块存在。装门只有 `install_llm_gate` 一个入口 ——
生产和测试的最小 app 共用这一处，测试不另写一份注册。
"""
from __future__ import annotations

import logging

from adapters.llm_adapter import set_call_guard
from core.scheduling import submit_to_main_loop
from deps import get_storage
from web.geo_guard import check_api_allowed
from core.request_context import LLM_CALLER, SYSTEM

logger = logging.getLogger("charsim.llm_gate")


class LLMCallerMissing(RuntimeError):
    """出站时上下文里没有调用方身份 —— fail-closed。

    「读不到 = 放行」会让请求之外的线程/任务拿到一条无人看守的出站路。真要在
    请求之外调用，唯一合法的写法是显式声明 `with system_llm_context():`，
    而不是让门猜。

    不带自定义字段（文案在抛出点拼）：这类异常 `cls(*args)` 本就重建得回来，
    无需 `__reduce__`，也不必进序列化登记表。
    """


def geo_refusal(ip: str | None, base_url: str) -> str | None:
    """纯判定：被拦返回理由，放行返回 None。

    **全仓唯一**调用 `check_api_allowed` 的地方。纯是刻意的 —— 它被两条路走到
    （调用点门、保存配置时的检查），判定本身不该带「记了几次」这种历史。
    """
    allowed, reason = check_api_allowed(ip, base_url)
    return None if allowed else reason


def geo_call_guard(base_url: str) -> str | None:
    """注入进适配器的那条守卫（契约：`fn(base_url) -> str | None`，返回值就是理由）。

    - 上下文里没有身份 → `LLMCallerMissing`（fail-closed）；
    - `SYSTEM` → 放行（`is` 判定，见 request_context）；
    - 否则 → `geo_refusal(caller.ip, base_url)`，**被拦时先记审计再返回理由**。

    审计挂在这扇门上而不是异常出口：出口不唯一（chat 的宽 except、SSE 错误帧、
    蒸馏的 `user_facing_error` 都各自收下这个异常，一条都不经过统一出口），而**每个
    调用点拒绝都必经这里** —— 挂在门上才是「恰好一次」。身份也在这里现成（`Caller`），
    出了这扇门只剩 `LLMCallRefused` 上的 base_url 与理由。
    """
    caller = LLM_CALLER.get(None)
    if caller is None:
        raise LLMCallerMissing(
            f"出站前拿不到调用方身份（base_url={base_url!r}）—— 门 fail-closed；"
            "请求之外的调用请显式声明 core.request_context.system_llm_context()"
        )
    if caller is SYSTEM:
        return None
    reason = geo_refusal(caller.ip, base_url)
    if reason:
        emit_geo_block_audit(caller.user_id, caller.ip, base_url, reason)
    return reason


def emit_geo_block_audit(user_id: str | None, ip: str | None, base_url: str, reason: str) -> None:
    """记一次「这个调用被 geo 拦了」。**全仓唯一**调用 `record_geo_block` 的地方。

    经投递原语异步写（`wait=False`，不拖住请求）。写入失败只记日志，**不改变判定** ——
    拦下这个调用是合规要求，审计是记录；让审计库的抖动把 403 变成 500，等于用记录的
    失败否掉了判定本身。所以这里自己吞，不让异常冒到调用方。
    """
    try:
        coro = get_storage().record_geo_block(user_id, ip, base_url, reason)
    except Exception as exc:
        logger.warning("Record geo block failed (non-fatal): %s", exc)
        return
    try:
        submit_to_main_loop(coro, wait=False)
    except Exception as exc:
        coro.close()  # 没投出去就得自己收尾，否则「coroutine was never awaited」
        logger.warning("Record geo block failed (non-fatal): %s", exc)


def install_llm_gate(app) -> None:
    """装配入口：把守卫注册进适配器。生产（`web/server.py`）与测试最小 app 共用这一个。

    取 *app* 是为了与 `register_domain_error_handlers` 同形 —— 装配层「装什么」集中
    在一处，将来门若有 app 级状态（如按 app 覆盖）也不必再改调用方签名。
    """
    set_call_guard(geo_call_guard)


__all__ = [
    "LLMCallerMissing", "geo_refusal", "geo_call_guard", "emit_geo_block_audit",
    "install_llm_gate",
]
