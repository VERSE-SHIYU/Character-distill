"""Character card export: SillyTavern v2 JSON and other formats."""

from __future__ import annotations

import json

from core.arc_view import card_outline, phase_header
from core.schema import CharacterCard

_SPEECH_PREFIX = "speaking_style."          # 说话风格单列进 mes_example，不重复进人设正文
_SPEECH_LAYERS = ("stable", "state", "experience")   # 说话风格整组列出：含全程不变的用词水平、禁忌用词


def to_tavern_json(card: CharacterCard, first_message: str = "") -> dict:
    """Convert a CharacterCard to SillyTavern character-card-v2 spec.

    Args:
        card: Structured character card from the distiller.
        first_message: Override greeting; falls back to ``card.first_message``.

    Returns:
        Dict conforming to ``chara_card_v2`` / ``spec_version: "2.0"``.

    全程成立的部分写一遍，阶段特有的内容**只在其所属阶段的段落里**出现一次（`card_outline`
    的全程 + 逐阶段两层；字段名一律取登记表的 ``label``）。人设正文与说话风格（mes_example）
    走同一套，说话风格也按阶段列出（R4）。
    """
    def sectioned(layers: tuple[str, ...], speech: bool) -> list[str]:
        """全程一段 + 逐阶段一段（有内容才出表头）；``speech`` 选说话风格或其余人设。"""
        lifelong, per_phase = card_outline(card, layers)

        def lines(rows) -> list[str]:
            return [f"{label}：{text}" for path, label, text in rows
                    if path.startswith(_SPEECH_PREFIX) == speech]

        out = lines(lifelong)
        for i, (phase, rows) in enumerate(zip(card.character_arc.phases, per_phase), 1):
            phase_lines = lines(rows)
            if phase_lines:                      # 只在那阶段成立的内容，别从导出里消失
                out.append(phase_header(i, phase.label))
                out += phase_lines
        return out

    personality_text = "\n".join(sectioned(("state", "experience"), speech=False))

    description_lines: list[str] = [card.identity]
    if card.background:
        description_lines.append(card.background)
    description_lines.append(personality_text)
    description = "\n\n".join(description_lines)

    # 说话风格与人设正文同一套「全程 + 逐阶段」，字段由登记表遍历，不在此手列（R4）。
    mes_example = "\n".join(sectioned(_SPEECH_LAYERS, speech=True))

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
