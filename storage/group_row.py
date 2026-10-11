"""群会话行 —— 列清单、写入映射与 JSON 列编解码，两个后端共用的**唯一**一处。

`card_ids`（成员卡 id 列表）与 `member_phases`（卡 id → 建群时选的阶段号）在库里都是 JSON 文本。
读的列清单、写的「列名 → 值」、JSON 的编解码都只在这里写一次；新加一个 JSON 列只改这一个文件。
"""

from __future__ import annotations

import json
from typing import Any

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


def group_insert_values(
    *, id: str, name: str, card_ids: list[str], user_id: str,
    user_persona_type: str, user_persona_card_id: str,
    user_persona_name: str, user_persona_desc: str,
    member_phases: dict[str, int] | None,
) -> dict[str, Any]:
    """建群 INSERT 的「列名 → 值」。**插入顺序即列顺序** —— 两个 store 的 `create_group_session`
    照着它拼列名串与占位符（PG `$1..$n` / SQLite `?`），列名与取值都不再各写一遍；JSON 列
    （`card_ids`、`member_phases`）也在这里编一次。以后建群多写一列只改这里。
    """
    return {
        "id": id,
        "name": name,
        "card_ids": json.dumps(card_ids),
        "user_id": user_id,
        "user_persona_type": user_persona_type,
        "user_persona_card_id": user_persona_card_id,
        "user_persona_name": user_persona_name,
        "user_persona_desc": user_persona_desc,
        "member_phases": encode_member_phases(member_phases),
    }


def decode_group_row(d: dict) -> dict:
    """把一行群会话里的 JSON 列解开（原地改并返回）。只解这一行里有的列。"""
    d["card_ids"] = json.loads(d["card_ids"])
    if "member_phases" in d:
        d["member_phases"] = {
            str(k): int(v) for k, v in json.loads(d["member_phases"] or "{}").items()}
    return d
