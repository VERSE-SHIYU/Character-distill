"""群会话行 —— 列清单与 JSON 列编解码，两个后端共用的**唯一**一处。

`card_ids`（成员卡 id 列表）与 `member_phases`（卡 id → 建群时选的阶段号）在库里都是 JSON 文本。
解码只在这里写一次，两个 store 的每个读点都调它；新加一个 JSON 列只改这一个文件。
"""

from __future__ import annotations

import json

# 群会话表的完整列清单 —— 两个后端所有「读整行」的 SELECT 都引用它。
# 顺序即 SELECT 顺序；以后给这张表加一列只改这里（列本身的迁移另算）。
GROUP_SESSION_COLUMNS = (
    "id", "name", "card_ids", "user_id", "created_at", "deleted_at",
    "user_persona_type", "user_persona_card_id", "user_persona_name",
    "user_persona_desc", "user_avatar_data", "member_phases",
)
GROUP_SESSION_SELECT = ", ".join(GROUP_SESSION_COLUMNS)


def encode_member_phases(member_phases: dict[str, int] | None) -> str:
    """建群时写库用。None / 空 → '{}'（= 全员最后阶段）。"""
    return json.dumps({str(k): int(v) for k, v in (member_phases or {}).items()})


def decode_group_row(d: dict) -> dict:
    """把一行群会话里的 JSON 列解开（原地改并返回）。只解这一行里有的列。"""
    d["card_ids"] = json.loads(d["card_ids"])
    if "member_phases" in d:
        d["member_phases"] = {
            str(k): int(v) for k, v in json.loads(d["member_phases"] or "{}").items()}
    return d
