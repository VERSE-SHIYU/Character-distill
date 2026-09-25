# -*- coding: utf-8 -*-
"""逐片名单的汇总 / 并组 / 判主次 / 取理由 —— **纯计算，不 import 任何项目模块**。

为什么单独一个模块：这些是 `Distiller` 的**下游**纯函数（`Distiller` 在模块顶层 import
本模块）。放进 `core/character_roster.py` 就成了层级倒置 —— 那个模块是名单生命周期层，
位于 `Distiller` **之上**（它 import `Distiller` 的 `DistillError`，文件头写明「不管 LLM
细节」），`Distiller` 反过来依赖它只能靠函数内 import，把环藏起来而不是消掉。

分层：本模块不认识 LLM、不认识提示词、不认识名单缓存，只认识
`{name, aliases, importance, reason}` 这个形状（与红线 2 的对外形状同一份）与
`{chunk, name, aliases, importance, reason}` 这个逐片条目形状。

**歧义判定时机**（本模块的中心）：并组做完之后、按**最终分组**计数。放在并组之前会
误伤同一人的多个主名 —— 片1 贾宝玉{宝玉,宝二爷}、片2 宝玉{宝二爷} 里，「宝玉」既是
别名又是另一片的主名，按「挂在几个主名上」数它挂在两个名下，会被当泛称丢掉，两个人
也并不到一起。按组数就不会：别名等于另一个主名、且此刻只挂在一个组上 → 两组并，做到
不动点；并完只剩一组，「宝二爷」就只指向一个人，该留。
"""

from __future__ import annotations

from typing import Any, NamedTuple, Sequence

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
    漏了就等于把此人在那些分片里的场景判给了别人。歧义别名的移除不在这里，在并组
    全部做完之后（`_strip_ambiguous`）。
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


def _alias_owners(groups: Sequence[dict[str, Any]]) -> dict[str, set[int]]:
    """别名 → 挂着它的组下标集合。「挂在几组上」是歧义的唯一判据。"""
    owners: dict[str, set[int]] = {}
    for i, group in enumerate(groups):
        for alias in group["aliases"]:
            owners.setdefault(alias, set()).add(i)
    return owners


def group_identify_entries(
    per_chunk: Sequence[Sequence[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """逐片识别条目 → 分组。

    两条并组规则，都是代码可判的：同一 `name` 必归一组；**别名等于另一个主名、且此刻
    只挂在一个组上**时两组并，反复做到不动点（上一轮的并组会让某些别名从挂两组变挂一组，
    所以判据每轮重算）。歧义别名（挂在 ≥2 个组上，如「二爷」同时指两个人）不据此并组；
    它从组里移除不在这一步，见 `merge_identify_groups` 收尾的 `_strip_ambiguous`。
    """
    obs = _observations(per_chunk)

    # 别名 → 列过它的主名（去重、保序）
    owners: dict[str, list[str]] = {}
    for o in obs:
        for alias in o.aliases:
            bucket = owners.setdefault(alias, [])
            if o.name not in bucket:
                bucket.append(o.name)

    listed: dict[str, list[str]] = {}
    for o in obs:
        bucket = listed.setdefault(o.name, [])
        for alias in o.aliases:
            if alias not in bucket:
                bucket.append(alias)

    order: list[str] = list(dict.fromkeys(o.name for o in obs))
    names = set(order)
    uf = _UnionFind(order)
    while True:
        grew = False
        for alias, who in owners.items():
            if alias not in names:
                continue    # 不是主名的别名没有对端可并
            roots = {uf.find(n) for n in who}
            if len(roots) != 1:
                continue    # 挂在 ≥2 个组上 = 有歧义，不并
            root = next(iter(roots))
            if uf.find(alias) != root:
                uf.union(alias, root)
                grew = True
        if not grew:
            break

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


def alias_prompt_rows(
    groups: Sequence[dict[str, Any]],
) -> list[tuple[str, int, tuple[str, ...]]]:
    """送给别名判断模型的清单：每组的主名、出现分片数、别名。

    别名里去掉**当时**挂在 ≥2 组上的那些 —— 泛称（「二爷」）摆进清单只会诱导模型
    把两个人并成一个。这里算的是并组前的悬挂情况，因为模型要判的正是「这组和那组
    是不是同一人」；并组之后再算，泛称会因为只剩一组而显得「唯一」，就白送了。
    """
    owners = _alias_owners(groups)
    return [
        (group["name"], group["chunk_count"],
         tuple(a for a in group["aliases"] if len(owners[a]) < 2))
        for group in groups
    ]


def _strip_ambiguous(groups: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """按**最终**分组移除歧义别名 —— 对每一个组都做，包括没被合并、原样返回的组。

    只在被合并的组上做会漏掉绝大多数：模型回 `[]`（谁都不用并）时全是单组，
    歧义别名会一个不剩地留在名单里，污染下游的子串匹配。
    """
    owners = _alias_owners(groups)
    ambiguous = {alias for alias, idxs in owners.items() if len(idxs) >= 2}
    if not ambiguous:
        return list(groups)
    return [
        {**group, "aliases": tuple(a for a in group["aliases"] if a not in ambiguous)}
        for group in groups
    ]


def merge_identify_groups(
    groups: Sequence[dict[str, Any]], pairs: Sequence[Sequence[str]],
) -> list[dict[str, Any]]:
    """按别名判断给出的组对并组，然后按最终分组移除歧义别名。

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
    return _strip_ambiguous(merged)


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
