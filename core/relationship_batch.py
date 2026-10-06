# -*- coding: utf-8 -*-
"""关系分批 —— 切批、并行、汇总、失败口径的**唯一一处**（§4.5，锁 S8/S9）。

为什么分批：关系条数随登场人数增长（主角几十人），一次调用出全部会把输出顶到 token 上限。
为什么单独成模块：四个蒸馏入口都要这条链，散在入口里必然漂移。

**本模块不认识 `Distiller`**（导入方向 §4.0 / 锁 S11）：调用模型的函数由调用方注入
（蒸馏时是 `_collect_stream` 的绑定），前缀也由调用方给 —— 与主调用用同一个前缀函数，
缓存才命中得上。失败口径：任一批失败 → 整步失败（`RelationshipBatchError`），不静默丢人。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from core import concurrency as C  # 派生与上下文传播（ctx_submit）

# 每批的人数。由输出 token 上限导出（§2.3），不由价格导出；**只此一处定义**（锁 S8）。
REL_BATCH_SIZE = 10


class RelationshipBatchError(RuntimeError):
    """某一批关系生成失败 —— 整步失败，不把这一批的人悄悄丢掉。"""


# 关系生成的口径（五条）：**只在这里写一次**（锁 S13），由 `_batch_prompt` 引用。
# 主调用维度 F 的「只出名单」讲的是「这一步先别写关系详情」，这五条讲的是「这一步怎么写」，
# 是同一件事的两步、两句话 —— 规则若也内联进 distiller 的维度说明就成了「一处两写」。
RELATIONSHIP_RULES = (
    "## 关系生成口径\n"
    "1. 单向视角：只写主角怎么看对方，不写对方怎么看主角。\n"
    "2. note 是喂给聊天模型的固定立场：一句话讲清我和ta是什么关系、我怎么看ta。\n"
    "3. 只写态度变了的阶段：态度没变的阶段不写；从头到尾一个态度就只写一条。\n"
    "4. 没有阶段时 phase 填 0。\n"
    "5. quote 是该阶段里的原文摘录（10-40字，逐字照抄）。"
)


def _batch_prompt(prefix: str, batch: list[str], phases: list[str]) -> str:
    """本批的提示词：调用方的 `prefix` 逐字在前（前缀一致缓存才命中），后面只换本批人物。

    `prefix` 是**共享前缀**（正文 / 组共享段），不含本步指令 —— 见 `_relationships_batched`
    的调用点与锁 S14：把主调用的系统提示整段拿来，维度 F 的「只出名单」会被带进来。
    """
    stage = "、".join(p for p in phases if p) or "（无阶段）"
    return (
        f"{prefix}\n\n"
        f"只产出这几个人物与主角的关系，其余人不要出现：{'、'.join(batch)}\n"
        f"阶段依次为：{stage}；每条关系按阶段给态度。\n"
        f"{RELATIONSHIP_RULES}\n"
        "输出 JSON 数组，每个元素含 target / relation / attitude / note，"
        "以及 attitudes: [{phase, attitude, quote, note}]。"
    )


def batch_relationships(targets: list[str], phases: list[str], *,
                        stream_call: Callable[[str, list[str]], Any],
                        prefix: str, batch_size: int = REL_BATCH_SIZE) -> list[dict]:
    """按 `batch_size` 切批、批间并行，汇总成一份关系列表（顺序与 `targets` 一致）。

    `stream_call(prompt, batch)` 由调用方注入，返回本批的关系条目（字典或模型皆可）。
    任一批抛异常 → `RelationshipBatchError`，整步失败。
    """
    batches = [targets[i:i + batch_size] for i in range(0, len(targets), batch_size)]
    if not batches:
        return []

    def _run(batch: list[str]) -> list:
        return list(stream_call(_batch_prompt(prefix, batch, phases), batch) or [])

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        futures = [C.ctx_submit(pool, _run, b) for b in batches]
        for i, fut in enumerate(futures):
            try:
                out.extend(fut.result())
            except Exception as exc:
                raise RelationshipBatchError(f"第 {i + 1} 批关系生成失败：{exc}") from exc
    return out
