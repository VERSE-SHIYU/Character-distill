# -*- coding: utf-8 -*-
"""缺陷 74 的锁：嵌入凭据（key / region）在唯一出口的**行为**契约。

病根：同一份「读 `embedding_key` / `embedding_region` 并给默认值」在三族里各抄了一遍，
默认值还不一致 —— A 族（chat / history / group / distill 的会话段）给 `"cn"`，B 族
（distill 的四个蒸馏端点）给 `""`。两处不一致各自都能跑，所以没人发现：B 族那个 `""`
一旦流到 `core/rag.py` 的 `config.get("embedding_region", "cn")`，键**在**而值为空，
`.get` 返回 `""` 而非默认值，`DASHSCOPE_BASE_URLS[""]` 当场 KeyError。

锁钉的是出口的行为 —— 默认值归一到什么、谁优先。**不钉「谁在调用它」**：某处改回
手搓但默认值写对，不是缺陷（写错则由本判据与出口兜住，后果是当场 KeyError、不是静默）；
而扫源码位置就得维护一份白名单，白名单自身还有盲区（单引号写法、下标读取照样漏），
维护成本压在判据的可信度上。
"""
from __future__ import annotations


def test_resolve_embedding_contract():
    """出口的默认值与优先级。

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
