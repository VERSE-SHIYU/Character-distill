"""Character card export: SillyTavern v2 JSON and other formats."""

from __future__ import annotations

import json

from core.schema import CharacterCard


def to_tavern_json(card: CharacterCard, first_message: str = "") -> dict:
    """Convert a CharacterCard to SillyTavern character-card-v2 spec.

    Args:
        card: Structured character card from the distiller.
        first_message: Override greeting; falls back to ``card.first_message``.

    Returns:
        Dict conforming to ``chara_card_v2`` / ``spec_version: "2.0"``.
    """
    style = card.speaking_style

    personality_lines: list[str] = []
    if card.personality_traits:
        personality_lines.append("性格：" + "；".join(card.personality_traits))
    if card.values:
        personality_lines.append("价值观：" + "；".join(card.values))
    if card.key_memories:
        personality_lines.append("关键记忆：" + "；".join(card.key_memories))
    if card.inner_tensions:
        personality_lines.append("内在矛盾：" + "；".join(card.inner_tensions))
    personality_text = "\n".join(personality_lines)

    description_lines: list[str] = [card.identity]
    if card.background:
        description_lines.append(card.background)
    description_lines.append(personality_text)
    description = "\n\n".join(description_lines)

    speech_lines: list[str] = []
    if style.tone:
        speech_lines.append(f"语气：{style.tone}")
    if style.sentence_pattern:
        speech_lines.append(f"句式：{style.sentence_pattern}")
    if style.vocabulary_level:
        speech_lines.append(f"用词：{style.vocabulary_level}")
    if style.catchphrases:
        speech_lines.append("口癖：" + "、".join(style.catchphrases))
    if style.taboo_words:
        speech_lines.append("禁忌用词：" + "、".join(style.taboo_words))
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
