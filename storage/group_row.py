"""群会话行的 JSON 列编解码 —— 两个后端共用的**唯一**一处。

`card_ids`（成员卡 id 列表）与 `member_phases`（卡 id → 建群时选的阶段号）在库里都是 JSON 文本。
解码只在这里写一次，两个 store 的每个读点都调它；新加一个 JSON 列只改这一个文件。
"""

from __future__ import annotations

import json


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
