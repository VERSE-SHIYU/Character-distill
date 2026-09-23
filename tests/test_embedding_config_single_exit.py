# -*- coding: utf-8 -*-
"""单一出口锁：嵌入配置（key / region）的读取与默认值只在**一处**归一（缺陷 74）。

病根：同一份「读 `embedding_key` / `embedding_region` 并给默认值」在三族里各抄了一遍，
默认值还不一致 —— A 族（chat / history / group / distill 的会话段）给 `"cn"`，B 族
（distill 的四个蒸馏端点）给 `""`，C 族（mcp / rebuild 脚本）走 env。两处不一致各自都能
跑，所以没人发现：B 族那个 `""` 一旦流到 `core/rag.py` 的
`config.get("embedding_region", "cn")`，键**在**而值为空，`.get` 返回 `""` 而非默认值，
`DASHSCOPE_BASE_URLS[""]` 当场 KeyError。

两条判据各自可独立失败：

  1. **不在别处手搓** —— 全仓 `core/ web/ mcp_server/ scripts/` 里，形如
     `.get("embedding_key"` / `.get("embedding_region"` 的取值只许出现在三处**角色**位置：
     归一出口（`web/llm_resolution.py`）、最终消费点（`core/rag.py`）、账号展示与排障面
     （`web/routers/auth.py`）。列的是**位置**不是站点 —— 站点清单本身就是漂移点。
     判据是「命中集合 ⊆ 白名单」（方向是子集，不是相等）：新增一处手搓即红；
     某个白名单文件将来不再需要这个写法，不会误报。
  2. **出口的契约** —— `resolve_embedding` 的 region 缺省恒为 `"cn"`（不是 `""`），
     且用户 key 优先于 env key。这一条钉住「默认值分歧在这一处归一」的那个值本身。

**判据的 ceiling（有意）**：按字面量 `.get("` 扫，单引号写法 `.get('embedding_region'`
漏得掉。判据面向的是「读并给默认值」这个形态 —— 给错默认值必然写成 `.get(名, 默认)`，
下标读取（`cfg["embedding_region"]`）没有默认值可给，故不在本判据面内。

**扫描范围不含 `tests/`**：测试替身本来就要手工拼配置字典，那是构造不是生产读取。
`scripts/` **在**范围内 —— C 族里就有它一处。
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

_SRC_DIRS = ("core", "web", "mcp_server", "scripts")
_EXIT_REL = "web/llm_resolution.py"       # 归一出口
_CONSUMER_REL = "core/rag.py"             # 最终消费点（构造 DashScopeEmbedding）
_ACCOUNT_REL = "web/routers/auth.py"      # /me 展示 + /test-embedding 排障
_ALLOWED = {_EXIT_REL, _CONSUMER_REL, _ACCOUNT_REL}

#: 「读并给默认值」的字面形态。两族原本的写法都长这样（只差默认值）。
_PATTERNS = ('.get("embedding_key"', '.get("embedding_region"')


def _sources(overrides: dict[str, str] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in _SRC_DIRS:
        base = REPO_ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            out[p.relative_to(REPO_ROOT).as_posix()] = p.read_text(encoding="utf-8")
    if overrides:
        out.update(overrides)
    return out


def _hand_rolled(sources: dict[str, str]) -> list[str]:
    return sorted(rel for rel, src in sources.items()
                  if any(pat in src for pat in _PATTERNS))


def test_no_hand_rolled_embedding_read_outside_the_single_exit():
    """判据 1：手搓取值只许落在三处角色位置。"""
    hits = _hand_rolled(_sources())

    # 白名单三条必须都还在扫到 —— 否则判据面被悄悄缩没了，锁在假绿。
    missing = sorted(_ALLOWED - set(hits))
    assert not missing, (
        f"这些位置不再出现取值写法，判据面缩水了：{missing}。"
        "要么它们被搬走了（请同步本判据），要么扫描范围写错了 —— 无论哪种，锁现在不成立。"
    )

    stray = sorted(set(hits) - _ALLOWED)
    assert not stray, (
        f"这些文件又开始手搓嵌入配置的读取与默认值：{stray}。\n"
        "修法：改用 web.llm_resolution.resolve_embedding —— region 缺省在那一处归一为 "
        '"cn"（不是 ""：DASHSCOPE_BASE_URLS 里没有空键，"" 会 KeyError）。'
    )


def test_resolve_embedding_contract():
    """判据 2：出口的默认值与优先级。

    纯函数，没有数据库、没有环境变量、没有 HTTP —— env 由实参给。
    """
    from web.llm_resolution import Source, resolve_embedding

    got = resolve_embedding({})
    assert got.source is Source.UNAVAILABLE and got.key == "" and got.region == "cn"

    got = resolve_embedding({"embedding_key": "k"})
    assert got.source is Source.USER and got.key == "k" and got.region == "cn"

    # 病灶本尊：B 族原本写成 .get("embedding_region", "")。空串必须归一到 "cn"，
    # 否则下游 `DASHSCOPE_BASE_URLS[""]` 当场 KeyError（.get 对「键在而值为空」不给默认）。
    got = resolve_embedding({"embedding_key": "k", "embedding_region": ""})
    assert got.region == "cn", f'空 region 没归一到 "cn"，实得 {got.region!r}'

    got = resolve_embedding({}, env_key="e")
    assert got.source is Source.GLOBAL_ENV and got.key == "e" and got.region == "cn"

    # 用户 key 优先：一旦用户 key 胜出，region 也归用户侧的缺省 —— env 的 intl 不能
    # 配到用户的 cn key 上（地域绑定，混用必 401）。
    got = resolve_embedding({"embedding_key": "k"}, env_key="e", env_region="intl")
    assert got.source is Source.USER and got.key == "k" and got.region == "cn", (
        f"用户 key 没压过 env key，实得 {got}"
    )


def test_mutation_reverting_a_site_to_hand_rolled_goes_red():
    """变异自测：把 group.py 的注入点改回手搓并给错默认值 → 判据 1 红。

    变异形态按「修复前的代码长什么样」写（缺陷 74 的 A 族原文），不按「哪里能动」写。
    """
    rel = "web/routers/group.py"
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    anchor = "    emb = resolve_embedding(user_cfg)\n"
    assert anchor in src, f"变异锚点未命中（{rel} 里这行改了？）—— 自测失效"
    mutated = src.replace(
        anchor,
        '    if user_cfg.get("embedding_key"):\n'
        '        rag_config["embedding_key"] = user_cfg["embedding_key"]\n'
        '        rag_config["embedding_region"] = user_cfg.get("embedding_region", "intl")\n',
        1,
    )
    assert mutated != src

    stray = sorted(set(_hand_rolled(_sources({rel: mutated}))) - _ALLOWED)
    assert rel in stray, f"改回手搓后判据 1 没红 —— 锁是假的，实际命中：{stray}"
