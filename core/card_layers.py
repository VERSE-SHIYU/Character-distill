# -*- coding: utf-8 -*-
"""登记表：所有进 prompt 的字段在这里按「类别」登记一次 —— 唯一来源。

类别决定投影规则（`core.arc_view.project_card` 按本表通用执行）：

- ``stable``      稳定：原样，不随阶段变。
- ``state``       状态：阶段 k（或沿用最近一次）那个人的样子。阶段 k 那格没值时沿用 1..k 里
                  最近一个有值的阶段；列表 = 沿用来的那一格 + 顶层；单值 = 阶段 k 有值用之，
                  其次顶层，都没有才沿用早期阶段（2026-10-09，见 docs/specs/state-inertia.md）。
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
    """一个字段的投影类别、形态与中文名（``label`` 是后端唯一的字段名来源）。"""
    layer: str
    kind: str
    label: str


LAYERS = ("stable", "state", "experience", "custom", "none")
KINDS = ("list", "scalar", "map")   # map 只用于 none 层（未定位区的同形 overlay）


# 键 = 附录 B 的路径（点号；`CharacterCard` 叶子全集，多一个少一个 U1 都红）。
REGISTRY: dict[str, FieldSpec] = {
    # ── 稳定（原样）────────────────────────────────────────────────────
    "name": FieldSpec("stable", "scalar", "名字"),
    "identity": FieldSpec("stable", "scalar", "身份"),
    "speaking_style.vocabulary_level": FieldSpec("stable", "scalar", "用词水平"),
    "speaking_style.taboo_words": FieldSpec("stable", "list", "禁忌用词"),
    "background": FieldSpec("stable", "scalar", "背景"),
    "psyche.openness": FieldSpec("none", "scalar", "开放性"),
    "psyche.conscientiousness": FieldSpec("none", "scalar", "尽责性"),
    "psyche.extraversion": FieldSpec("none", "scalar", "外向性"),
    "psyche.agreeableness": FieldSpec("stable", "scalar", "宜人性"),
    "psyche.neuroticism": FieldSpec("none", "scalar", "神经质"),
    "psyche.affinity_baseline": FieldSpec("stable", "scalar", "好感基线"),
    "psyche.volatility": FieldSpec("stable", "scalar", "情绪波动"),
    "psyche.grudge_inertia": FieldSpec("stable", "scalar", "记仇惯性"),
    "cognitive.education_level": FieldSpec("stable", "scalar", "教育程度"),
    "cognitive.vocabulary_level": FieldSpec("stable", "scalar", "词汇水平"),
    "psyche.agreeableness_facets": FieldSpec("stable", "list", "宜人性分面"),
    # ── 状态（列表：顶层 + 阶段 k）─────────────────────────────────────
    "personality_traits": FieldSpec("state", "list", "性格特征"),
    "values": FieldSpec("state", "list", "核心价值观"),
    "motives": FieldSpec("state", "list", "动机"),
    "inner_tensions": FieldSpec("state", "list", "内在矛盾"),
    "emotional_patterns": FieldSpec("state", "list", "情感模式"),
    "speaking_style.catchphrases": FieldSpec("state", "list", "口癖"),
    "psyche.triggers": FieldSpec("state", "list", "雷点"),
    "psyche.soft_spots": FieldSpec("state", "list", "软肋"),
    "psyche.warming_conditions": FieldSpec("state", "list", "亲近条件"),
    "dialogue_examples": FieldSpec("state", "list", "对话示例"),
    # ── 状态（单值：阶段 k 有值用之，其次顶层，都无才沿用最近一次）─────────
    "decision_style": FieldSpec("state", "scalar", "决策风格"),
    "speaking_style.tone": FieldSpec("state", "scalar", "语气"),
    "speaking_style.sentence_pattern": FieldSpec("state", "scalar", "句式"),
    "cognitive.speech_style": FieldSpec("state", "scalar", "说话风格"),
    "psyche.relational_modes.close": FieldSpec("state", "scalar", "对亲近的人"),
    "psyche.relational_modes.normal": FieldSpec("state", "scalar", "对平常的人"),
    "psyche.relational_modes.conflict": FieldSpec("state", "scalar", "起冲突时"),
    # ── 经历（顶层 + 1..k）────────────────────────────────────────────
    "key_memories": FieldSpec("experience", "list", "关键记忆"),
    "cognitive.knowledge_scope": FieldSpec("experience", "scalar", "知识范围"),
    # ── 专门投影（各自函数）───────────────────────────────────────────
    "character_arc.axis": FieldSpec("custom", "scalar", "变化轴"),
    "character_arc.phases": FieldSpec("custom", "list", "角色弧线"),
    "relationships": FieldSpec("custom", "list", "人际关系"),
    "first_message": FieldSpec("custom", "scalar", "开场白"),
    "situation_behaviors": FieldSpec("custom", "list", "情境→行为"),
    # ── 不进 prompt ───────────────────────────────────────────────────
    "tags": FieldSpec("none", "list", "标签"),
    "awakening_message": FieldSpec("none", "scalar", "苏醒台词"),
    "character_arc.source_fingerprint": FieldSpec("none", "scalar", "源码指纹"),
    # 未定位区：阶段核对不上的状态类条目，只展示、不进 prompt（`CharacterArc.unlocated`）
    "character_arc.unlocated.behaviors": FieldSpec("none", "list", "未定位·做法"),
    "character_arc.unlocated.overlay": FieldSpec("none", "map", "未定位·字段"),
    "character_arc.unlocated.attitudes": FieldSpec("none", "list", "未定位·态度"),
}


def paths_of(*layers: str) -> list[str]:
    """登记表里属于给定类别的路径（`ArcPhase.overlay` 校验与投影都从这里取）。"""
    wanted = set(layers)
    return [p for p, s in REGISTRY.items() if s.layer in wanted]


def overlay_leaves(overlay, prefix: str = ""):
    """把与卡片同形的 overlay 摊成 ``(登记表路径, 值)``：遇到字典往下走，其余都是叶子。

    overlay 与卡片同形（``{"speaking_style": {"catchphrases": [...]}}``），所以叶子路径就是
    登记表路径 —— 读写一律用 `get_path` / `set_path`，不把路径当键名存。
    """
    for key, val in (overlay or {}).items():
        path = f"{prefix}{key}"
        if isinstance(val, dict):
            yield from overlay_leaves(val, f"{path}.")
        else:
            yield path, val


def check_overlay(overlay: dict, *, layers: tuple[str, ...], lists_only: bool, where: str) -> None:
    """阶段 overlay 与未定位区 overlay 的**唯一**校验器（两处都是「与卡片同形的部分卡片」）。

    ① 任何一层的键名不许含「.」—— 路径只能靠嵌套表达（根因锁，spec §3.5）；
    ② 每个叶子路径须是登记表里 `layers` 类别的路径；
    ③ 值形态：`lists_only`（未定位区）一律列表；否则按 `kind`（list → 列表，scalar → 字符串）。
    """
    def keys(node):
        for k, v in node.items():
            yield k
            if isinstance(v, dict):
                yield from keys(v)

    for key in keys(overlay):
        if "." in key:
            raise ValueError(f"{where} 键名不许含「.」（路径要嵌套写）：{key}")
    for path, val in overlay_leaves(overlay):
        spec = REGISTRY.get(path)
        if spec is None or spec.layer not in layers:
            raise ValueError(f"{where} 键未登记（须为 {'/'.join(layers)} 路径）：{path}")
        if (lists_only or spec.kind == "list") and not isinstance(val, list):
            raise ValueError(f"{where}[{path}] 应为列表")
        if not lists_only and spec.kind == "scalar" and not isinstance(val, str):
            raise ValueError(f"{where}[{path}] 应为字符串")


def nest_flat_keys(overlay: dict) -> dict:
    """旧数据（#116 之后、本次之前）把登记表路径当键名存：``{"speaking_style.catchphrases": …}``。

    转成与卡片同形的嵌套字典 —— **唯一转换点**在 `ArcPhase` 的加载校验里调用。已有嵌套值时
    不覆盖（同一路径两种写法并存时以嵌套为准）。
    """
    out: dict = {}
    for key, val in overlay.items():
        if "." not in key:
            out[key] = val
    for key, val in overlay.items():
        if "." in key and pydash.get(out, key) is None:
            pydash.set_(out, key, val)
    return out


def get_path(obj, path: str):
    """按点号路径读嵌套字段（pydash 薄封装，路径形态见登记表）。"""
    return pydash.get(obj, path)


def set_path(obj, path: str, value) -> None:
    """按点号路径写嵌套字段。"""
    pydash.set_(obj, path, value)
