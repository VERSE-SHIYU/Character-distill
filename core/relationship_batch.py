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
    "3. 只写态度变了的阶段：第一条写在两人开始有交集的那个阶段；之后态度没变的阶段不写；"
    "从头到尾一个态度就只写一条。\n"
    "4. 没有阶段时 phase 填 0。\n"
    "5. quote 是该阶段里的原文摘录（10-40字，逐字照抄）。\n"
    "6. attitudes 里每一条都写 note：那一阶段的口径（同第 2 条）。\n"
    "7. 顶层的 attitude / note 写两人最初的关系，不写后来的变化。"
)


# 补跑那一次额外点明的一句：名单是我们给的，模型只需**逐字照抄** target，别改名、别用别称。
# 名字对不上就看不出「谁还没回」，于是同一个人被当成缺人反复补、或反过来静默丢掉。
_EXACT_TARGET_NOTE = "target 逐字使用上面名单里的写法：不要改名、不要用别称。"


def _batch_prompt(prefix: str, name: str, batch: list[str], phases: list[str], *,
                  material: str = "", exact: bool = False) -> tuple[str, str]:
    """本批的 ``(system, user)``：关系这一步的指令**全部**在这里，调用方只给共享前缀。

    - system = 调用方的 `prefix`（共享段：正文 / 组共享段，逐字在前，缓存才命中）+ 本步指令；
    - user = 本步自己的请求（点名主角与本批人物）；`material` 非空时（分组路径的分析档案）
      放在请求前面 —— 一次读完路径的素材是正文，已在 prefix 里，这里为空。

    不接收调用方的 messages：主调用的用户消息是「生成角色卡 / 输出角色卡」，带进来就和
    「只输出关系数组」冲突（R1，与 B2 同根：本步指令不能有一半是从调用方继承的）。缓存只认
    前缀，user 换成本步自己的不影响命中。

    `exact=True` 时多一句「逐字照抄名单里的写法」（补跑那次用）。
    """
    stage = "、".join(p for p in phases if p) or "（无阶段）"
    people = "、".join(batch)
    exact_line = f"{_EXACT_TARGET_NOTE}\n" if exact else ""
    system = (
        f"{prefix}\n\n"
        f"## 本步：写「{name}」的人际关系\n"
        f"主角是「{name}」。只产出这几个人物与主角的关系，其余人不要出现：{people}\n"
        f"{exact_line}"
        f"阶段依次为：{stage}；每条关系按阶段给态度。\n"
        f"{RELATIONSHIP_RULES}\n"
        "输出 JSON 数组，每个元素含 target / relation / attitude / note，"
        "以及 attitudes: [{phase, attitude, quote, note}]。"
    )
    source = f"以下是关于「{name}」的分析档案：\n\n{material}\n\n" if material else ""
    user = f"{source}请写出「{name}」与这几个人物的关系：{people}。只输出 JSON 数组。"
    return system, user


def _fan_out(batches: list[list[str]], run: Callable[[list[str]], list]) -> list:
    """并行跑各批并汇总（顺序 = 批序）；任一批抛异常 → `RelationshipBatchError`，整步失败。"""
    out: list = []
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        futures = [C.ctx_submit(pool, run, b) for b in batches]
        for i, fut in enumerate(futures):
            try:
                out.extend(fut.result())
            except Exception as exc:
                raise RelationshipBatchError(f"第 {i + 1} 批关系生成失败：{exc}") from exc
    return out


def batch_relationships(targets: list[str], phases: list[str], *,
                        stream_call: Callable[[str, str, list[str]], Any],
                        prefix: str, name: str, material: str = "",
                        batch_size: int = REL_BATCH_SIZE) -> list[dict]:
    """按 `batch_size` 切批、批间并行，汇总成一份关系列表（顺序与 `targets` 一致）。

    `stream_call(system, user, batch)` 由调用方注入，返回本批的关系条目（dict 列表）；
    `system` / `user` 都由本模块的 `_batch_prompt` 产出，调用方只发不改。
    任一批抛异常 → `RelationshipBatchError`，整步失败。

    **完整性**（B2）：名单是**我们发出的**，模型只需逐字照抄 `target`（提示词里就这么写）。
    故比对是**逐字字符串**：模型漏掉名单里的人，一次性补不齐就等于静默丢人 —— 缺人只对缺的
    那些**补跑一次**（那次提示里点明「逐字使用名单里的写法」）；补跑后仍缺 →
    `RelationshipBatchError` 点名是谁。名单外的条目（写错名 / 多写的）一律丢弃。

    **不解析别名**：别名→标准名要名单（`{name, aliases}`），蒸馏入口拿不到（要读存储）；
    分三步走的这条路里，第一步给名单、第二步照名单写，名字本就是同一批字符串，逐字比对上。
    """
    want: list[str] = []
    seen: set[str] = set()
    for t in targets:
        if t and t not in seen:
            seen.add(t)
            want.append(t)
    if not want:
        return []

    found: dict[str, dict] = {}

    def _merge(rows: list) -> None:
        for row in rows or []:
            key = row.get("target")
            if key in seen and key not in found:
                found[key] = dict(row)

    def _run(batch: list[str], *, exact: bool = False) -> list:
        system, user = _batch_prompt(prefix, name, batch, phases,
                                     material=material, exact=exact)
        return list(stream_call(system, user, batch) or [])

    _merge(_fan_out([want[i:i + batch_size] for i in range(0, len(want), batch_size)], _run))
    missing = [k for k in want if k not in found]
    if missing:
        _merge(_fan_out(
            [missing[i:i + batch_size] for i in range(0, len(missing), batch_size)],
            lambda b: _run(b, exact=True)))
        still = [k for k in want if k not in found]
        if still:
            raise RelationshipBatchError(f"关系生成缺少人物：{'、'.join(still)}")
    return [found[k] for k in want]
