# -*- coding: utf-8 -*-
"""角色名单的唯一入口：读缓存 → 识别 → 落库，收敛到一处。

另收口**「从名单里挑目标角色」的唯一判据** —— ``target_character_name``。
识别失败与「挑不出目标角色」是两回事（前者是故障，后者是名单本身没有可用角色），
但都抛 ``DistillError`` 家族、共用同一条上屏出口；三通道各自只渲染，不各自判断。

**前置条件（三个函数都适用）**：调用方**已经**用 `get_text_owned(text_id, user_id)`
做过属主校验。本模块不重复校验 —— 它也无法校验：`get_characters_owned` 对「无缓存」
与「非属主」都返回 None，本模块区分不出，而顺序反过来就等于没有校验（distill 路由
踩过，见缺陷 19）。

分层：本模块只做名单的生命周期（读 / 算 / 写），不管 LLM 细节（`Distiller` 的活）、
不管 HTTP（路由的活）、不管卡片与蒸馏。名单是**作品的属性**，不是某个会话或某条
请求的属性 —— 所以它不参与会话状态。

为什么要有一个入口：这段「读缓存 → 没命中就跑识别 → 写回」原先只在 `/identify`
一处，而 `/start`、`/run_stream`、`/reindex`、`TextManager.distill_all` 各自**只跑
识别、不读缓存也不写回** —— 同一份名单被反复重算。缓存键是（text_id、属主、**识别
算法版本**），版本不符即当无缓存（见 `Distiller.IDENTIFY_VERSION`）：口径一改，旧
名单必须自己失效，否则残缺的旧名单会被一直当全书名单用。
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.distiller import DistillError, Distiller


# 「名单里挑不出目标角色」的两种情形。常量而非散落的字符串字面量：reason 是
# 判据的产物，文案表与判据同处一地。
NO_TARGET_EMPTY = "empty"                 # 名单为空
NO_TARGET_MISSING_NAME = "missing_name"   # 首项没有 name

# 上屏文案**只写在这一处** —— 三个通道（HTTP / bg 任务 / SSE）都经
# `user_facing_error(exc)` 取它，不各自维护一份 reason→文案的表。改一个字，
# 三处同时变（这正是「同一个判据只能有一处」的延伸）。
_NO_TARGET_MESSAGES: dict[str, str] = {
    NO_TARGET_EMPTY: "未识别到任何角色",
    NO_TARGET_MISSING_NAME: "识别结果缺少角色名",
}


class NoTargetCharacter(DistillError):
    """名单里挑不出目标角色。

    这是「名单为空 / 首项缺 name 怎么办」的**唯一判据** —— 原先 HTTP 那层有一份
    「取第一个名字」的辅助函数，bg 线程与流式端点各自手抄了同样的两段分支。
    三份判据会在「什么叫没有目标角色」上漂移。

    继承 ``DistillError``：它与识别失败**共用同一条上屏出口**，因此 HTTP 拿到
    400 + ``user_message``（``web/server.py::_domain_error_status`` 按 MRO 命中），
    bg 任务状态与 SSE 帧经 ``user_facing_error`` 拿到同一份文案。各通道于是只需
    渲染，不需要自己判断该说什么。

    ``reason`` 仍保留：它是判据的可判定产物（供测试与日志分辨两种情形），
    但**不再是各通道查文案的键**。
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(_NO_TARGET_MESSAGES[reason], f"reason={reason}")

    def __reduce__(self):
        # 基类的 args 是格式化后的 message，重建时会被当成 reason 去查表 → KeyError。
        # 自定义状态的异常必须自带可重建路径（tests/test_exception_pickle_lock.py）。
        return (self.__class__, (self.reason,))


def target_character_name(chars: list[dict[str, Any]]) -> str:
    """名单里的第一个角色名。

    名单为空、或首项缺 ``name``，都抛 ``NoTargetCharacter``（带 ``reason``）。
    注意与「识别失败」区分：识别失败在 ``Distiller`` 那层就抛 ``DistillError``
    了，走不到这里 —— 能走到这里的名单一定是一次成功的识别结果。
    """
    if not chars:
        raise NoTargetCharacter(NO_TARGET_EMPTY)
    name = chars[0].get("name", "")
    if not name:
        raise NoTargetCharacter(NO_TARGET_MISSING_NAME)
    return name


async def cached_characters(
    storage: Any, text_id: str, user_id: str,
) -> list[dict[str, Any]] | None:
    """读名单缓存：无缓存 / 非属主 / 版本不符，三者都是 None。"""
    return await storage.get_characters_owned(
        text_id, user_id, version=Distiller.IDENTIFY_VERSION)


async def resolve_characters(
    storage: Any,
    distiller: Distiller,
    text_id: str,
    user_id: str,
    content: str,
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """取这部作品的名单：命中缓存即返回（不发 LLM），未命中才识别一次并写回。

    ``refresh=True`` 跳过缓存强制重算并覆盖（用户显式点「重新识别」时用）。
    识别是同步阻塞的 LLM 调用，这里统一丢进线程，调用方不必各自 `to_thread`。
    """
    if not refresh:
        cached = await cached_characters(storage, text_id, user_id)
        if cached:
            return cached
    chars = await asyncio.to_thread(distiller.identify_characters, content)
    await storage.save_characters(
        text_id, chars, version=Distiller.IDENTIFY_VERSION)
    return chars


def aliases_for(chars: list[dict[str, Any]], name: str) -> list[str]:
    """某人的别名；名单里没有此人（或此人无别名）时返回空列表。

    下游按子串用别名（蒸馏选片、RAG 打标签），所以别名只该来自识别结果里
    **唯一指向此人**的称呼（规则见 `core.distiller.ALIAS_UNIQUENESS_RULE`）。
    """
    for c in chars:
        if c.get("name") == name:
            return c.get("aliases") or []
    return []
