"""Character card export: SillyTavern v2 JSON and other formats."""

from __future__ import annotations

import json

from core.arc_view import card_outline, phase_header
from core.card_layers import REGISTRY
from core.schema import CharacterCard

_SPEECH_PREFIX = "speaking_style."          # 说话风格单列进 mes_example，不重复进人设正文


def _label(path: str) -> str:
    """字段中文名的唯一来源：登记表（B5）。"""
    return REGISTRY[path].label


def to_tavern_json(card: CharacterCard, first_message: str = "") -> dict:
    """Convert a CharacterCard to SillyTavern character-card-v2 spec.

    Args:
        card: Structured character card from the distiller.
        first_message: Override greeting; falls back to ``card.first_message``.

    Returns:
        Dict conforming to ``chara_card_v2`` / ``spec_version: "2.0"``.

    全程成立的部分写一遍，阶段特有的内容**只在其所属阶段的段落里**出现一次（`card_outline`
    的全程 + 逐阶段两层；字段名一律取登记表的 ``label``）。
    """
    style = card.speaking_style
    lifelong, per_phase = card_outline(card)

    def body(rows) -> list[str]:
        return [f"{label}：{text}" for path, label, text in rows
                if not path.startswith(_SPEECH_PREFIX)]

    personality_lines = body(lifelong)
    for i, (phase, rows) in enumerate(zip(card.character_arc.phases, per_phase), 1):
        phase_body = body(rows)
        if phase_body:                           # 只在那阶段成立的内容，别从导出里消失
            personality_lines.append(phase_header(i, phase.label))
            personality_lines += phase_body
    personality_text = "\n".join(personality_lines)

    description_lines: list[str] = [card.identity]
    if card.background:
        description_lines.append(card.background)
    description_lines.append(personality_text)
    description = "\n\n".join(description_lines)

    speech_lines: list[str] = []
    if style.tone:
        speech_lines.append(f"{_label('speaking_style.tone')}：{style.tone}")
    if style.sentence_pattern:
        speech_lines.append(f"{_label('speaking_style.sentence_pattern')}：{style.sentence_pattern}")
    if style.vocabulary_level:
        speech_lines.append(f"{_label('speaking_style.vocabulary_level')}：{style.vocabulary_level}")
    if style.catchphrases:
        speech_lines.append(f"{_label('speaking_style.catchphrases')}：" + "、".join(style.catchphrases))
    if style.taboo_words:
        speech_lines.append(f"{_label('speaking_style.taboo_words')}：" + "、".join(style.taboo_words))
    mes_example = "\n".join(speech_lines)

    greeting = first_message or card.first_message or f"你好，我是{card.name}。"

    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": card.name,
            "description": description,
            "personality": personality_text,
            "scenario": "",
            "first_mes": greeting,
            "mes_example": mes_example,
            "creator_notes": card.background,
            "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": [],
            "tags": [],
            "creator": "",
            "character_version": "1.0",
            "extensions": {},
        },
    }


def export_tavern_json(card: CharacterCard, first_message: str = "") -> str:
    """Serialize a CharacterCard as pretty-printed SillyTavern JSON.

    Args:
        card: Structured character card.
        first_message: Override greeting.

    Returns:
        Indented UTF-8 JSON string.
    """
    return json.dumps(
        to_tavern_json(card, first_message),
        ensure_ascii=False,
        indent=2,
    )
