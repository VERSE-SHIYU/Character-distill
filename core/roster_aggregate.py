# -*- coding: utf-8 -*-
"""逐片名单的汇总 / 并组 / 判主次 / 取理由 —— **纯计算，不 import 任何项目模块**。

为什么单独一个模块：这些是 `Distiller` 的**下游**纯函数（`Distiller` 在模块顶层 import
本模块）。放进 `core/character_roster.py` 就成了层级倒置 —— 那个模块是名单生命周期层，
位于 `Distiller` **之上**（它 import `Distiller` 的 `DistillError`，文件头写明「不管 LLM
细节」），`Distiller` 反过来依赖它只能靠函数内 import，把环藏起来而不是消掉。

分层：本模块不认识 LLM、不认识提示词、不认识名单缓存，只认识
`{name, aliases, importance, reason, speak_chunks}` 这个形状（与红线 2 的对外形状同一份）与
`{chunk, name, aliases, spoke, reason}` 这个逐片条目形状。

**歧义判定时机**（本模块的中心）：并组做完之后、按**最终分组**计数。放在并组之前会
误伤同一人的多个主名 —— 片1 贾宝玉{宝玉,宝二爷}、片2 宝玉{宝二爷} 里，「宝玉」既是
别名又是另一片的主名，按「挂在几个主名上」数它挂在两个名下，会被当泛称丢掉，两个人
也并不到一起。按组数就不会：别名等于另一个主名、且此刻只挂在一个组上 → 两组并，做到
不动点；并完只剩一组，「宝二爷」就只指向一个人，该留。

**成员主名优先**（判定「挂在几组上」的口径）：某组的成员主名只属于那一组。片1 贾宝玉
{宝玉}、片2 宝玉{宝二爷}、片3 甄宝玉{宝玉} 并组后，「宝玉」是合并组的成员主名、又被
甄宝玉组当别名 —— 只数别名会把它当泛称从两边一起移除，合并组因此丢掉最常用的称呼，
下游按子串选片就漏掉此人的大部分片。判据与清单过滤共用一份，见 `_visible_aliases`。
"""

from __future__ import annotations

from typing import Any, Iterable, NamedTuple, Sequence

#: 判「主要」的说话分片数门槛：卡片里的说话风格与对话示例直接取自原文对话，铁律 1
#: 要求一个特质至少在 2 个不同场景出现、核心性格至少 3 个 ⇒ 至少 6 个不同分片里
#: 有本人说话，才可能蒸出一张性格准确的卡。出场多但几乎不说话的（「和尚」形态）
#: 出不了卡，判次要。
MIN_SPEAK_CHUNKS = 6


class _Obs(NamedTuple):
    """一条逐片识别条目：哪个分片说的、说了什么、本人有没有亲口说话。"""

    chunk: int
    name: str
    aliases: tuple[str, ...]
    spoke: bool
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


def usable_alias(alias: str) -> bool:
    """别名可用判据：**至少两个字**。多分片汇总与单分片路径都调这一处。

    单字称呼在下游按**子串**匹配（蒸馏选片的 `match_terms = [name] + aliases`、
    RAG 打标签）时几乎命中每一片 —— 实测「他」覆盖 212/213 片，等于把全书都判给
    了一个人。主名不适用本判据：那是模型认定的名字，不是称呼。
    """
    return len(alias) >= 2


def _observations(per_chunk: Sequence[Sequence[dict[str, Any]]]) -> list[_Obs]:
    """逐片条目 → 观测列表，按分片序、片内原序。

    模型在不同分片里可能多打一个空格，去空白之后才是同一个人。没有 `name` 的条目
    直接丢（归不进任何组，下游也没有谁要它），别名等于主名的、以及单字的（判据见
    `usable_alias`）也丢。
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
                if a and a != name and usable_alias(a)
            ))
            out.append(_Obs(
                chunk, name, tuple(aliases), item.get("spoke") is True, item.get("reason")))
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
    impersonal: bool = False,
) -> dict[str, Any]:
    """成员 + 该组列过的别名 + 观测 → 一个组。

    组的 `aliases` =（列过的别名 ∪ 成员名）− 显示名：并组之后另一个成员名就是同一个
    人的另一个称呼，下游按子串匹配（`match_terms = [name] + aliases`）时不能漏掉它 ——
    漏了就等于把此人在那些分片里的场景判给了别人。歧义别名的移除不在这里，在并组
    全部做完之后（`_strip_ambiguous`）。

    `speak_count` 是本人**亲口说话**的不同分片数（判主次只用它）；`impersonal` 由
    全书判定给出，缺省 False —— 逐片那条路给不出这个信息。
    """
    entries = tuple(sorted(entries, key=lambda o: o.chunk))
    name = _display_name(members, entries)
    return {
        "name": name,
        "members": tuple(members),
        "aliases": tuple(sorted((set(listed_aliases) | set(members)) - {name})),
        "listed_aliases": tuple(listed_aliases),
        "chunk_count": len({o.chunk for o in entries}),
        "speak_count": len({o.chunk for o in entries if o.spoke}),
        "impersonal": impersonal,
        "entries": entries,
    }


def _visible_aliases(groups: Sequence[dict[str, Any]]) -> list[tuple[str, ...]]:
    """每个组该对外的别名 —— 清单过滤与最终移除**共用这一份**口径，不写两份。

    「挂在几组上」数的是各组的主名、成员主名与别名，判据三条：

      - 该称呼是某组的成员主名 → 只属于那一组：本组保留（即使它不是显示名），
        其余组的 `aliases` 里移除。有分片以它为主名列出此人，它就不是泛称 ——
        别组拿它当别名，不足以让它降格；
      - 只以别名身份出现、且挂在 ≥2 个组上（如「二爷」同时指两个人）→ 从所有组移除；
      - 其余（只挂一个组）→ 保留。

    只数 `aliases` 会把前一种误判成后一种：片1 贾宝玉{宝玉}、片2 宝玉{宝二爷}、
    片3 甄宝玉{宝玉} 并组后，「宝玉」在合并组与甄宝玉组的别名里各挂一次 → 两边一起
    移除 → 合并组丢掉最常用的称呼，下游按子串选片就漏掉此人的大部分片。
    """
    owners: dict[str, set[int]] = {}
    member_of: dict[str, int] = {}
    for i, group in enumerate(groups):
        for name in group["members"]:
            owners.setdefault(name, set()).add(i)
            member_of[name] = i     # 成员互不相交，同一主名只可能属于一个组
        for alias in group["aliases"]:
            owners.setdefault(alias, set()).add(i)

    visible: list[tuple[str, ...]] = []
    for i, group in enumerate(groups):
        kept: list[str] = []
        for alias in group["aliases"]:
            owner = member_of.get(alias)
            if owner is None:
                if len(owners[alias]) < 2:
                    kept.append(alias)
            elif owner == i:
                kept.append(alias)
        visible.append(tuple(kept))
    return visible


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

    口径与最终移除同一份（`_visible_aliases`）：清单里显示什么，最后就留什么。
    """
    visible = _visible_aliases(groups)
    return [
        (group["name"], group["chunk_count"], visible[i])
        for i, group in enumerate(groups)
    ]


def _strip_ambiguous(groups: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """按**最终**分组移除不属于本组的称呼 —— 对每一个组都做，包括没被合并、原样返回的组。

    只在被合并的组上做会漏掉绝大多数：模型回 `[]`（谁都不用并）时全是单组，
    泛称会一个不剩地留在名单里，污染下游的子串匹配。判据见 `_visible_aliases`。
    """
    visible = _visible_aliases(groups)
    return [
        {**group, "aliases": visible[i]} for i, group in enumerate(groups)
    ]


def merge_identify_groups(
    groups: Sequence[dict[str, Any]], pairs: Sequence[Sequence[str]],
    impersonal_names: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """按组对并组，然后按最终分组移除不属于本组的称呼，并定下各组的 `impersonal`。

    **模型没判为同一人的一律保持分开** —— 只并它点名的对，不做任何推断式合并。
    `pairs` 里写错的名字（不在名单中的主名）忽略：那是模型输出，认错一个名字不该让
    整本书的识别失败。

    `impersonal_names` 按**并组前**的组名标注；并完之后只有「所并各组全为泛称」才算
    泛称 —— 一个真人物并进泛称组，那组就是具体的人，反之亦然。
    """
    impersonals = set(impersonal_names)
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
        imp = all(groups[j]["name"] in impersonals for j in idxs)
        if len(idxs) == 1:
            merged.append({**group, "impersonal": imp})
            continue
        merged.append(_assemble_group(
            tuple(dict.fromkeys(m for j in idxs for m in groups[j]["members"])),
            tuple(dict.fromkeys(a for j in idxs for a in groups[j]["listed_aliases"])),
            tuple(o for j in idxs for o in groups[j]["entries"]),
            impersonal=imp,
        ))
    return _strip_ambiguous(merged)


def tally_judge_samples(
    samples: Sequence[dict[str, Any]], votes: int,
) -> tuple[list[tuple[str, str]], set[str]]:
    """全书判定的多份样本 → （生效的合并组对，生效的泛称主名），各自需 ≥ `votes` 票。

    单次调用的例外清单随采样大幅漂移（同一输入同一提示词两次结果不一致），故按
    self-consistency（Wang et al., ICLR 2023）计多数票定结果。

    每条 merge 拆成**两两组对**：模型写 `["甲","乙","丙"]` 说的是这仨同一个人，拆开
    是它的等价含义。同一个组对在同一份样本里只计一票（模型可能重复列或在一份里同时
    给 `[甲,乙]` 与 `[甲,乙,丙]`），跨样本才累加。空名与单名条目不计。
    """
    merge: dict[tuple[str, str], int] = {}
    impersonal: dict[str, int] = {}
    for sample in samples:
        seen_pairs: set[tuple[str, str]] = set()
        for entry in sample.get("merge") or []:
            if not isinstance(entry, list):
                continue
            names = [n for n in (_norm(x) for x in entry) if n]
            for a in range(len(names)):
                for b in range(a + 1, len(names)):
                    seen_pairs.add(tuple(sorted((names[a], names[b]))))
        for pair in seen_pairs:
            merge[pair] = merge.get(pair, 0) + 1
        seen_names = {n for n in (_norm(x) for x in sample.get("impersonal") or []) if n}
        for name in seen_names:
            impersonal[name] = impersonal.get(name, 0) + 1
    return (
        sorted(pair for pair, count in merge.items() if count >= votes),
        {name for name, count in impersonal.items() if count >= votes},
    )


def _reason(entries: tuple[_Obs, ...]) -> str:
    """该组本人说话的分片里的第一条理由；没有则第一条非空理由。都没有则是空串。"""
    for o in entries:
        if o.spoke and _norm(o.reason):
            return _norm(o.reason)
    for o in entries:
        reason = _norm(o.reason)
        if reason:
            return reason
    return ""


def finalize_identify_roster(
    groups: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """分组 → 对外名单：判主次、取理由、排序。不截断（逐片识别出的人全保留）。

    判主次只看两个代码可数的输入：本人说话的不同分片数（门槛见 `MIN_SPEAK_CHUNKS`）
    与全书判定标的 `impersonal`。出场分片数不参与 —— 出场多但几乎不说话的
    （「和尚」形态）蒸不出性格准确的卡。

    泛称组（`impersonal`）标次要但仍在名单里：RAG 按别名打标签要用全名单，删掉会让
    它指向的片段整段丢失归属。
    """
    scored = []
    for group in groups:
        importance = "主要" if (
            group["speak_count"] >= MIN_SPEAK_CHUNKS and not group["impersonal"]
        ) else "次要"
        scored.append((importance, group, _reason(group["entries"])))
    # 主要在前；组内按 (说话分片数, 出现的分片数) 降序。并列保持首现序（sorted 稳定），
    # 名单因此可复现。
    scored.sort(
        key=lambda s: (s[0] != "主要", -s[1]["speak_count"], -s[1]["chunk_count"]))
    return [
        {"name": g["name"], "aliases": list(g["aliases"]), "importance": importance,
         "reason": reason, "speak_chunks": g["speak_count"]}
        for importance, g, reason in scored
    ]
