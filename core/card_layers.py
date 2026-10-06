# -*- coding: utf-8 -*-
"""登记表：所有进 prompt 的字段在这里按「类别」登记一次 —— 唯一来源。

类别决定投影规则（`core.arc_view.project_card` 按本表通用执行）：

- ``stable``      稳定：原样，不随阶段变。
- ``state``       状态：阶段 k 那个人的样子。列表 = 顶层 + 阶段 k；单值 = 阶段 k 有值用之，
                  否则回落顶层。
- ``experience``  经历：顶层 + 阶段 1..k。列表拼接；单值用「；」连接。
- ``custom``      专门投影：各自函数（弧线 / 关系 / 开场白 / 情境→行为）。
- ``none``        不进 prompt。

``kind`` 只有 ``list`` / ``scalar`` 两种 —— `ArcPhase.overlay` 的键与类型校验按它判。

导入方向（§4.0，锁 S11）：本模块只可导入 `core.schema` 与 pydash。
"""
from __future__ import annotations

from typing import NamedTuple

import pydash

from core.schema import CharacterCard  # noqa: F401  （登记表覆盖它的叶子，导入作锚点）


class FieldSpec(NamedTuple):
    """一个字段的投影类别与形态。"""
    layer: str
    kind: str


LAYERS = ("stable", "state", "experience", "custom", "none")
KINDS = ("list", "scalar")


# 键 = 附录 B 的路径（点号；`CharacterCard` 叶子全集，多一个少一个 U1 都红）。
REGISTRY: dict[str, FieldSpec] = {
    # ── 稳定（原样）────────────────────────────────────────────────────
    "name": FieldSpec("stable", "scalar"),
    "identity": FieldSpec("stable", "scalar"),
    "speaking_style.vocabulary_level": FieldSpec("stable", "scalar"),
    "speaking_style.taboo_words": FieldSpec("stable", "list"),
    "background": FieldSpec("stable", "scalar"),
    "psyche.openness": FieldSpec("stable", "scalar"),
    "psyche.conscientiousness": FieldSpec("stable", "scalar"),
    "psyche.extraversion": FieldSpec("stable", "scalar"),
    "psyche.agreeableness": FieldSpec("stable", "scalar"),
    "psyche.neuroticism": FieldSpec("stable", "scalar"),
    "psyche.affinity_baseline": FieldSpec("stable", "scalar"),
    "psyche.volatility": FieldSpec("stable", "scalar"),
    "psyche.grudge_inertia": FieldSpec("stable", "scalar"),
    "cognitive.education_level": FieldSpec("stable", "scalar"),
    "cognitive.vocabulary_level": FieldSpec("stable", "scalar"),
    # ── 状态（列表：顶层 + 阶段 k）─────────────────────────────────────
    "personality_traits": FieldSpec("state", "list"),
    "values": FieldSpec("state", "list"),
    "inner_tensions": FieldSpec("state", "list"),
    "emotional_patterns": FieldSpec("state", "list"),
    "speaking_style.catchphrases": FieldSpec("state", "list"),
    "psyche.triggers": FieldSpec("state", "list"),
    "psyche.soft_spots": FieldSpec("state", "list"),
    "dialogue_examples": FieldSpec("state", "list"),
    # ── 状态（单值：阶段 k 有值用之，否则顶层）─────────────────────────
    "decision_style": FieldSpec("state", "scalar"),
    "speaking_style.tone": FieldSpec("state", "scalar"),
    "speaking_style.sentence_pattern": FieldSpec("state", "scalar"),
    "cognitive.speech_style": FieldSpec("state", "scalar"),
    # ── 经历（顶层 + 1..k）────────────────────────────────────────────
    "key_memories": FieldSpec("experience", "list"),
    "cognitive.knowledge_scope": FieldSpec("experience", "scalar"),
    # ── 专门投影（各自函数）───────────────────────────────────────────
    "character_arc.axis": FieldSpec("custom", "scalar"),
    "character_arc.phases": FieldSpec("custom", "list"),
    "relationships": FieldSpec("custom", "list"),
    "first_message": FieldSpec("custom", "scalar"),
    "situation_behaviors": FieldSpec("custom", "list"),
    # ── 不进 prompt ───────────────────────────────────────────────────
    "tags": FieldSpec("none", "list"),
    "awakening_message": FieldSpec("none", "scalar"),
    "character_arc.source_fingerprint": FieldSpec("none", "scalar"),
}


def paths_of(*layers: str) -> list[str]:
    """登记表里属于给定类别的路径（`ArcPhase.overlay` 校验与投影都从这里取）。"""
    wanted = set(layers)
    return [p for p, s in REGISTRY.items() if s.layer in wanted]


def get_path(obj, path: str):
    """按点号路径读嵌套字段（pydash 薄封装，路径形态见登记表）。"""
    return pydash.get(obj, path)


def set_path(obj, path: str, value) -> None:
    """按点号路径写嵌套字段。"""
    pydash.set_(obj, path, value)
