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
一处，而 `/start`、`/run_stream`、`/reindex`、`TextManager.distill_all`（已于
2026-09-22 删除，见 86）各自**只跑识别、不读缓存也不写回** —— 同一份名单被反复重算。
缓存键是（text_id、属主、**识别算法版本**），版本不符即当无缓存（见
`Distiller.IDENTIFY_VERSION`）：口径一改，旧名单必须自己失效，否则残缺的旧名单会被
一直当全书名单用。
"""

from __future__ import annotations

import asyncio
from typing import Any, NamedTuple, Sequence

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
) -> list[dict[str, Any]]:
    """取这部作品的名单：命中缓存即返回（不发 LLM），未命中才识别一次并写回。

    识别是同步阻塞的 LLM 调用，这里统一丢进线程，调用方不必各自 `to_thread`。
    """
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


# ── 逐片名单的汇总 / 并组 / 判主次（纯计算，不碰 LLM） ─────────────────────
#
# 逐分片识别各报各的名单：同一个人会在不同分片里被反复列出、各分片也看不到对方的
# 判断。原先这一步整包丢给模型（一份「读全部分片名单、写出整份全书名单」的提示词），
# 模型同时干三件事 ——
# 归组、按全书重判主次、重写理由。前两件都不需要判断力：归组是名字与别名的比对，
# 主次是计数（出现分片数、被逐片判为主要的分片数）。留在模型那儿的只剩一件真正
# 需要判断力的事：**哪些组其实是同一个人**（「贾宝玉」与「宝玉」在不同分片里各成
# 一组时，代码认不出来）。于是模型只输出组对，名单由下面的纯函数算出。
#
# 输入是「逐片条目」而不是提示词文本：本模块不认识 LLM 的输出格式，只认识
# `{name, aliases, importance, reason}` 这个形状（与红线 2 的对外形状同一份）。

_MAIN_MARK = "主"


class _Obs(NamedTuple):
    """一条逐片识别条目：哪个分片说的、说了什么。"""

    chunk: int
    name: str
    aliases: tuple[str, ...]
    importance: Any
    reason: Any


class _UnionFind:
    """并查集 —— 两处并组都是「按若干条等价关系求连通分量」：别名并组、模型给的组对。"""

    def __init__(self, keys: Sequence[Any]) -> None:
        self._parent = {k: k for k in keys}

    def find(self, key: Any) -> Any:
        parent = self._parent
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(self, a: Any, b: Any) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def _norm(value: Any) -> str:
    """名字 / 别名 / 理由的去空白规范化；None 与空串都归一成空串。"""
    return "" if value is None else str(value).strip()


def _observations(per_chunk: Sequence[Sequence[dict[str, Any]]]) -> list[_Obs]:
    """逐片条目 → 观测列表，按分片序、片内原序。

    模型在不同分片里可能多打一个空格，去空白之后才是同一个人。没有 `name` 的条目
    直接丢（归不进任何组，下游也没有谁要它），别名等于主名的也丢。
    """
    out: list[_Obs] = []
    for chunk, items in enumerate(per_chunk):
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _norm(item.get("name"))
            if not name:
                continue
            raw = item.get("aliases")
            aliases = list(dict.fromkeys(
                a for a in (_norm(x) for x in (raw if isinstance(raw, list) else ()))
                if a and a != name
            ))
            out.append(_Obs(
                chunk, name, tuple(aliases), item.get("importance"), item.get("reason")))
    return out


def _display_name(members: tuple[str, ...], entries: tuple[_Obs, ...]) -> str:
    """组的显示名：出现分片数最多的成员名；并列取最先出现的那个。

    并列必须有确定的取法 —— 否则同一份输入两次跑出不同的名字，名单不可复现。
    """
    chunks: dict[str, set[int]] = {m: set() for m in members}
    first_seen: dict[str, int] = {}
    for pos, obs in enumerate(entries):
        chunks[obs.name].add(obs.chunk)
        first_seen.setdefault(obs.name, pos)
    return max(members, key=lambda m: (len(chunks[m]), -first_seen[m]))


def _assemble_group(
    members: tuple[str, ...], listed_aliases: tuple[str, ...], entries: tuple[_Obs, ...],
) -> dict[str, Any]:
    """成员 + 该组列过的别名 + 观测 → 一个组。

    组的 `aliases` =（列过的别名 ∪ 成员名）− 显示名：并组之后另一个成员名就是同一个
    人的另一个称呼，下游按子串匹配（`match_terms = [name] + aliases`）时不能漏掉它 ——
    漏了就等于把此人在那些分片里的场景判给了别人。
    """
    entries = tuple(sorted(entries, key=lambda o: o.chunk))
    name = _display_name(members, entries)
    return {
        "name": name,
        "members": tuple(members),
        "aliases": tuple(sorted((set(listed_aliases) | set(members)) - {name})),
        "listed_aliases": tuple(listed_aliases),
        "chunk_count": len({o.chunk for o in entries}),
        "entries": entries,
    }


def group_identify_entries(
    per_chunk: Sequence[Sequence[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """逐片识别条目 → 分组。

    两条并组规则，都是代码可判的：同一 `name` 必归一组；某别名只在**一个**主名下
    出现过、且它本身也是另一个主名时，两个主名是同一人。别名在一批条目里挂在 ≥2 个
    不同主名下 = **有歧义**（「二爷」同时指两个人）：既不据此并组，也从所有组的
    `aliases` 里移除 —— 泛称留给谁都是给下游埋一个误匹配。是否有歧义只看这批条目的
    共现，不看词义（代码不做子串或语义推断）。
    """
    obs = _observations(per_chunk)

    owners: dict[str, list[str]] = {}
    for o in obs:
        for alias in o.aliases:
            bucket = owners.setdefault(alias, [])
            if o.name not in bucket:
                bucket.append(o.name)
    ambiguous = {alias for alias, who in owners.items() if len(who) >= 2}

    listed: dict[str, list[str]] = {}
    for o in obs:
        bucket = listed.setdefault(o.name, [])
        for alias in o.aliases:
            if alias not in ambiguous and alias not in bucket:
                bucket.append(alias)

    order: list[str] = list(dict.fromkeys(o.name for o in obs))
    names = set(order)
    uf = _UnionFind(order)
    for alias, who in owners.items():
        # 歧义别名不并组；不是主名的别名没有对端可并
        if alias not in ambiguous and alias in names:
            uf.union(who[0], alias)

    members_by_root: dict[str, list[str]] = {}
    for name in order:
        members_by_root.setdefault(uf.find(name), []).append(name)

    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in order:
        root = uf.find(name)
        if root in seen:
            continue
        seen.add(root)
        members = tuple(members_by_root[root])
        member_set = set(members)
        groups.append(_assemble_group(
            members,
            tuple(dict.fromkeys(a for m in members for a in listed.get(m, ()))),
            tuple(o for o in obs if o.name in member_set),
        ))
    return groups


def merge_identify_groups(
    groups: Sequence[dict[str, Any]], pairs: Sequence[Sequence[str]],
) -> list[dict[str, Any]]:
    """按别名判断给出的组对并组。

    **模型没判为同一人的一律保持分开** —— 只并它点名的对，不做任何推断式合并。
    `pairs` 里写错的名字（不在名单中的主名）忽略：那是模型输出，认错一个名字不该让
    整本书的识别失败。
    """
    by_name = {g["name"]: i for i, g in enumerate(groups)}
    uf = _UnionFind(range(len(groups)))
    for pair in pairs:
        idxs = [by_name[n] for n in pair if n in by_name]
        for other in idxs[1:]:
            uf.union(idxs[0], other)

    members_of: dict[int, list[int]] = {}
    for i in range(len(groups)):
        members_of.setdefault(uf.find(i), []).append(i)

    merged: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i, group in enumerate(groups):
        root = uf.find(i)
        if root in seen:
            continue
        seen.add(root)
        idxs = members_of[root]
        if len(idxs) == 1:
            merged.append(group)
            continue
        merged.append(_assemble_group(
            tuple(dict.fromkeys(m for j in idxs for m in groups[j]["members"])),
            tuple(dict.fromkeys(a for j in idxs for a in groups[j]["listed_aliases"])),
            tuple(o for j in idxs for o in groups[j]["entries"]),
        ))
    return merged


def _reason(entries: tuple[_Obs, ...], main_chunks: set[int]) -> str:
    """该组在「主要」分片里的第一条理由；没有则第一条非空理由。都没有则是空串。"""
    for o in entries:
        if o.chunk in main_chunks and _norm(o.reason):
            return _norm(o.reason)
    for o in entries:
        reason = _norm(o.reason)
        if reason:
            return reason
    return ""


def finalize_identify_roster(
    groups: Sequence[dict[str, Any]], total_chunks: int,
) -> list[dict[str, Any]]:
    """分组 → 对外名单：判主次、取理由、排序。不截断（逐片识别出的人全保留）。

    `total_chunks` 是**全书分片数**（不是有名单的分片数）—— 10% 那条门的分母是这个
    数；拿「该组出现的分片数」当分母，每个组都会轻易越过门槛。判据的三个输入全是
    代码可数的：被逐片判为主要的分片数、出现的不同分片数、全书分片数。逐片那一步的
    `importance` 只看得到本片，全书尺度必须在这里重判。

    「≥ 10%」写成整数乘法：浮点 0.1 在 total 不是 10 的倍数时会把边界判错。
    """
    scored = []
    for group in groups:
        main_chunks = {
            o.chunk for o in group["entries"] if _MAIN_MARK in _norm(o.importance)}
        importance = "主要" if (
            len(main_chunks) >= 2 or group["chunk_count"] * 10 >= total_chunks
        ) else "次要"
        scored.append((
            importance, len(main_chunks), group["chunk_count"],
            group, _reason(group["entries"], main_chunks),
        ))
    # 主要在前；组内按 (被逐片判为主要的分片数, 出现的分片数) 降序。并列保持首现序
    # （sorted 稳定），名单因此可复现。
    scored.sort(key=lambda s: (s[0] != "主要", -s[1], -s[2]))
    return [
        {"name": g["name"], "aliases": list(g["aliases"]),
         "importance": importance, "reason": reason}
        for importance, _main, _chunks, g, reason in scored
    ]
