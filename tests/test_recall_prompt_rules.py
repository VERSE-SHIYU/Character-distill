"""spec ㉑：起草提示词带「点明场合」「你 = 问答角色」「片段自足」三条；多轮带前两条（片段沿用原题）。

占位校验照旧通过（`render` 对未登记 / 缺失占位报错）。
"""
from core import recall_synth

OCCASION = "Pin down the occasion"
OCCASION_MULTITURN = "turn1 must also\n  pin down the occasion"
YOU = '"You" always means 甲.'
POV = "correct from 甲's own point of view"
SELF_CONTAINED = "The `should` passage alone must answer the question"
SPEAKER = "the speaker must be identifiable within the passage"


def _draft() -> str:
    return recall_synth.render(
        "draft", recall_synth.DRAFT_PROMPT, character="甲", title="书", window="窗口", feedback="")


def _multiturn() -> str:
    return recall_synth.render(
        "multiturn", recall_synth.MULTITURN_PROMPT, character="甲", title="书",
        question="你和乙去哪了？", entity="乙", feedback="")


def test_draft_prompt_carries_occasion_and_you_rules():
    text = _draft()
    assert OCCASION in text and YOU in text and POV in text
    assert SELF_CONTAINED in text and SPEAKER in text
    assert "{" not in text.replace('{"', "")


def test_multiturn_prompt_carries_occasion_for_turn1_and_you_rule():
    text = _multiturn()
    assert OCCASION_MULTITURN in text and YOU in text and POV in text
    assert "{" not in text.replace('{"', "")
