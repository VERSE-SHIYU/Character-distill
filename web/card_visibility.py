"""一张公开卡在详情页上能做什么 —— 唯一的规则表。

卡来自公开作品目录（`StorageBase.get_public_card`，只 PG 实现）：本地卡，或对端地区同步
过来的卡（隐私政策 3.2(2)）。两种视图：

======= ============================================================================
local   本地卡：一切照旧
remote  对端卡：本库只有它的内容副本，能「使用」（fork 成自己的私密卡）；赞、评论、版本、
        衍生、举报的数据都在对端库，不出境 —— 所以没有；源头在对端，本节点也不能下架
======= ============================================================================

能力**原样下发给前端**，前端只按能力渲染（缺省全关），不自己从 `is_remote` 推规则。
属主 / 写权限 / 管理员这些「谁在看」的判断不在这里 —— 它们与卡的来源无关，前端照旧组合。
放开对端卡的某项互动时，只改本文件的 `CAPABILITIES`。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class CardView(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True)
class CardCapabilities:
    """详情页每个动作 / 区块是否存在。字段名即下发给前端的键名。"""

    fork: bool     # 「使用角色」
    like: bool     # 点赞
    comment: bool  # 评论区与评论输入框
    history: bool  # 「版本历史」「衍生角色」两个标签页
    report: bool   # 举报
    moderate: bool  # 下架 / 删除（属主或管理员；对端卡的源头在对端，本节点不能删）


CAPABILITIES: dict[CardView, CardCapabilities] = {
    CardView.LOCAL: CardCapabilities(fork=True, like=True, comment=True, history=True, report=True, moderate=True),
    CardView.REMOTE: CardCapabilities(fork=True, like=False, comment=False, history=False, report=False,
                                      moderate=False),
}


def view_of(card: dict) -> CardView:
    return CardView.REMOTE if card.get("is_remote") else CardView.LOCAL


def page_meta(card: dict) -> dict:
    """卡详情响应里描述「这是哪种视图、能做什么」的两个键。"""
    view = view_of(card)
    return {"view": view.value, "capabilities": asdict(CAPABILITIES[view])}
