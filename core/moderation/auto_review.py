"""LLM-based auto review for character card content. Fails open on LLM errors."""

from __future__ import annotations

from typing import Any

from adapters.llm_adapter import LLMAdapter

_REVIEW_SYSTEM_PROMPT = (
    "你是一个内容审核助手。你收到的内容是角色卡 JSON，包含角色的设定、性格、背景等信息。"
    "请判断该角色卡是否包含以下违禁内容：\n"
    "1. 直接的色情描写或性行为细节\n"
    "2. 暴力、血腥、恐怖内容\n"
    "3. 政治敏感内容（攻击中国政府、领导人、政策等）\n"
    "4. 涉及未成年人的色情或不当内容\n\n"
    "请只回复一个 JSON 对象，不要包含其他文字：\n"
    '{"pass": true/false, "reason": "如果违规，简要说明原因；如果合规，填空字符串"}\n\n'
    "注意：仅当确定违规时才判为不通过。不确定时判为通过。"
)


async def auto_review_card(card_json: dict[str, Any], llm: LLMAdapter | None = None) -> dict[str, Any]:
    """Review a character card for policy violations.

    Args:
        card_json: The character card dict (name, personality, background, etc.)
        llm: Optional LLMAdapter instance. If None, imports the global one from deps.

    Returns:
        {"pass": bool, "reason": str}
        Fails open — returns {"pass": True, "reason": ""} on any error.
    """
    if llm is None:
        try:
            from deps import get_llm
            llm = get_llm()
        except Exception:
            return {"pass": True, "reason": ""}

    if llm is None:
        return {"pass": True, "reason": ""}

    # Flatten card_json into a reviewable text
    review_text = _flatten_card(card_json)

    try:
        result = await llm.achat(_REVIEW_SYSTEM_PROMPT, [{"role": "user", "content": review_text}])
        import json as _json
        parsed = _json.loads(result.strip())
        return {
            "pass": bool(parsed.get("pass", True)),
            "reason": str(parsed.get("reason", "")),
        }
    except Exception as exc:
        print(f"[auto_review] LLM review failed, defaulting to pass: {exc}")
        return {"pass": True, "reason": ""}


def _flatten_card(card_json: dict[str, Any]) -> str:
    """Convert card JSON to a flat text for LLM review."""
    import json as _json
    parts = []
    for key in ("name", "identity", "background", "personality"):
        val = card_json.get(key)
        if val:
            parts.append(f"{key}: {val}")
    for key in ("personality_traits", "values", "inner_tensions", "speaking_style"):
        val = card_json.get(key)
        if val:
            if isinstance(val, dict):
                parts.append(f"{key}: {_json.dumps(val, ensure_ascii=False)}")
            elif isinstance(val, list):
                parts.append(f"{key}: {'; '.join(str(v) for v in val)}")
            else:
                parts.append(f"{key}: {val}")
    # Include any other fields
    for k, v in card_json.items():
        if k not in ("name", "identity", "background", "personality", "personality_traits", "values", "inner_tensions", "speaking_style"):
            if isinstance(v, str) and len(v) > 20:
                parts.append(f"{k}: {v[:500]}")
    return "\n".join(parts) if parts else _json.dumps(card_json, ensure_ascii=False)
