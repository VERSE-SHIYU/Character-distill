# -*- coding: utf-8 -*-
"""D24 ⑤ 目标检查：relation 恢复「关系类型」口径。

根因：b57e9228 把关系详情移进 relationship_batch 时删掉了字段定义，模型不知道
relation 是「关系类型」，写成了叙述长句（还会用「她」指主角）。上限只在前端
REL_COLUMNS 写过。这里锁三件事：口径回归提示词、后端判据与前端上限一致、
补跑把不合口径的人一并重写。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from core.relationship_batch import batch_relationships, RelationshipBatchError

_ROOT = Path(__file__).resolve().parents[1]
_NOTE = "本角色弧线的阶段（略）。"


# ── 判据 ────────────────────────────────────────────────────────────────

def test_pronoun_relation_is_rejected():
    from core.relationship_batch import relation_violation

    why = relation_violation("被她一骗再骗却越陷越深的少年")
    assert why is not None and "她" in why


def test_overlong_relation_is_rejected():
    from core.relationship_batch import relation_violation

    why = relation_violation("名义上的表兄妹，实为互相试探、各怀机心的博弈对手")
    assert why is not None and "20" in why


def test_valid_relations_pass():
    from core.relationship_batch import relation_violation

    assert relation_violation("名义上的兄长（无血缘）") is None
    assert relation_violation("能窥见他人命数的高人") is None


# ── 提示词与前后端上限 ─────────────────────────────────────────────────

def test_rules_restore_relation_type_definition():
    from core.relationship_batch import REL_RELATION_MAX, RELATIONSHIP_RULES

    assert "关系类型" in RELATIONSHIP_RULES
    assert str(REL_RELATION_MAX) in RELATIONSHIP_RULES


def test_backend_limits_match_frontend_max_length():
    from core.relationship_batch import REL_ATTITUDE_MAX, REL_RELATION_MAX

    src = (_ROOT / "web/frontend/src/components/EditCardModal.jsx").read_text(encoding="utf-8")
    block = re.search(r"const REL_COLUMNS = \[(.*?)\]", src, re.S).group(1)
    limits = {m.group(1): int(m.group(2))
              for m in re.finditer(r"key:\s*'(\w+)'[^}]*?maxLength:\s*(\d+)", block)}
    assert limits["relation"] == REL_RELATION_MAX
    assert limits["attitude"] == REL_ATTITUDE_MAX


# ── 补跑：缺人与不合口径合成一次 ──────────────────────────────────────

def test_violating_relation_is_rewritten_in_retry():
    calls: list[tuple[list[str], str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append((list(batch), system))
        relation = ("被她一骗再骗却越陷越深的少年" if len(calls) == 1 else "青梅竹马")
        return [{"target": t, "relation": relation if t == "谢凛羽" else "友"}
                for t in batch]

    out = batch_relationships(["谢凛羽", "甲"], _NOTE, stream_call=fake,
                              prefix="P", name="主角")

    assert len(calls) == 2, "应在一次补跑里重写"
    assert calls[1][0] == ["谢凛羽"], "补跑只该含不合口径的人"
    assert "第 8 条" in calls[1][1] and "谢凛羽" in calls[1][1]
    assert {r["target"]: r["relation"] for r in out}["谢凛羽"] == "青梅竹马"


def test_still_violating_after_retry_warns_but_keeps_result(caplog):
    calls: list[list[str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append(list(batch))
        relation = ("被她一骗再骗却越陷越深的少年" if len(calls) == 1 else "她是我唯一的光")
        return [{"target": t, "relation": relation if t == "谢凛羽" else "友"}
                for t in batch]

    with caplog.at_level(logging.WARNING, logger="core.relationship_batch"):
        out = batch_relationships(["谢凛羽"], _NOTE, stream_call=fake,
                                  prefix="P", name="主角")

    assert {r["target"]: r["relation"] for r in out}["谢凛羽"] == "她是我唯一的光"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "谢凛羽" in warnings[0].getMessage() and "她" in warnings[0].getMessage()


def test_missing_and_violating_share_one_retry():
    calls: list[tuple[list[str], str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append((list(batch), system))
        if len(calls) == 1:
            # 甲星回了但 relation 是长句；乙星整个缺失。
            return [{"target": "甲星", "relation": "名义上的表兄妹，实为互相试探、各怀机心的博弈对手"}]
        return [{"target": t, "relation": "对手"} for t in batch]

    batch_relationships(["甲星", "乙星"], _NOTE, stream_call=fake, prefix="P", name="主角")

    assert len(calls) == 2, "缺人与违规应在同一次补跑里"
    assert set(calls[1][0]) == {"甲星", "乙星"}
    assert "逐字使用上面名单里的写法" in calls[1][1]
    assert "第 8 条" in calls[1][1]


def test_all_valid_makes_no_retry():
    calls: list[list[str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append(list(batch))
        return [{"target": t, "relation": "友"} for t in batch]

    batch_relationships(["甲", "乙"], _NOTE, stream_call=fake, prefix="P", name="主角")

    assert len(calls) == 1


def test_recovered_missing_with_bad_relation_still_warns(caplog):
    """补回来的人 relation 也不合口径时同样记 WARNING（他不在补跑前的违规名单里）。"""
    calls: list[list[str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append(list(batch))
        if len(calls) == 1:
            return []
        return [{"target": t, "relation": "她是我唯一的光"} for t in batch]

    with caplog.at_level(logging.WARNING, logger="core.relationship_batch"):
        out = batch_relationships(["谢凛羽"], _NOTE, stream_call=fake,
                                  prefix="P", name="主角")

    assert out[0]["relation"] == "她是我唯一的光"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "谢凛羽" in warnings[0].getMessage() and "她" in warnings[0].getMessage()


def test_missing_after_retry_still_raises():
    calls: list[list[str]] = []

    def fake(system: str, user: str, batch: list[str]):
        calls.append(list(batch))
        return [{"target": t, "relation": "友"} for t in batch if t != "乙"]

    with pytest.raises(RelationshipBatchError, match="乙"):
        batch_relationships(["甲", "乙"], _NOTE, stream_call=fake, prefix="P", name="主角")
    assert len(calls) == 2, "缺人补跑仍只一次"
