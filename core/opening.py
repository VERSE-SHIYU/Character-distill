# -*- coding: utf-8 -*-
"""开场白 / 苏醒台词 / 开场变体的提示词 —— **只此一处**（§4.6，锁 S12）。

三处调用方（新会话开场白、苏醒台词、开场变体）原先各写各的提示词，改一处漏两处。
本模块只产提示词字符串，不碰模型与存储：调用方拿到 prompt 自己调 LLM 记账（§4.0 的
导入方向 —— 本模块不认识路由、存储、`distiller`）。

**只收 `ProjectedCard`**（`core.arc_view.project_card` 的产物）：入参是原卡时提示词里会带上
阶段 k 之后才成立的人设（泄露），故这里直接抛 `TypeError`（DA18）。从原卡出发的路由走
`project_opening_prompt`，投影与拼 prompt 一次做完。
"""
from __future__ import annotations

from core.arc_view import ProjectedCard, project_card
from core.clock import UserClock, describe_time_period


def _require_projected(card) -> ProjectedCard:
    """拼 prompt 的入口只收投影卡 —— 收原卡在类型上直接堵死（DA18）。"""
    if not isinstance(card, ProjectedCard):
        raise TypeError(
            f"{type(card).__name__} 不是 ProjectedCard —— 拼角色扮演 prompt 只收 "
            f"core.arc_view.project_card 的产物")
    return card


def _phase_note(card: ProjectedCard) -> str:
    """投影卡的最后一段就是「此刻」：截断到阶段 k 时阶段表已截到 k。"""
    phases = card.character_arc.phases
    if not phases:
        return ""
    cur = phases[-1]
    head = cur.label or f"阶段 {len(phases)}"
    return f"此刻你处在这个阶段：{head}（{cur.state}）\n"


def build_opening_prompt(card: ProjectedCard, *, user_role: str = "") -> str:
    """新会话的第一句话的提示词（读投影卡：选阶段 k 时看不到 k 之后的口癖与人设）。"""
    card = _require_projected(card)
    style = card.speaking_style
    traits = "，".join(card.personality_traits[:3])
    seed = card.first_message or ""
    user_context = f"对「{user_role}」" if user_role else "对初次见面的陌生人"
    now = UserClock.now()
    period = describe_time_period(now.hour)
    seed_line = f"惯常开场白参考：「{seed}」\n" if seed else ""
    return (
        f"以「{card.name}」的口吻，{user_context}说此刻的第一句话。\n"
        f"身份：{card.identity}\n"
        f"性格：{traits}\n"
        f"语气：{style.tone}\n"
        f"口癖：{', '.join(style.catchphrases) if style.catchphrases else '无'}\n"
        f"{_phase_note(card)}"
        f"{seed_line}"
        f"当前时段：{period}（{now.hour}点）\n\n"
        f"先用不超过15字的括号动作把自己放进当下场景，再说话。"
        f"时间藏在语气里不点明。\n"
        f"(动作)台词，台词不超过50字。"
    )


def build_variation_prompt(card: ProjectedCard, *, max_chars: int = 50) -> str:
    """开场白变体的提示词（换措辞、不换口吻）。"""
    card = _require_projected(card)
    return (
        f"你是「{card.name}」。以下是你的标准开场白：\n"
        f"「{card.first_message}」\n\n"
        f"请用同样的语气、口癖和风格，重新说一句意思相近但措辞不同的开场白。"
        f"只输出开场白本身，不要解释。保持{card.name}的说话习惯。{max_chars}字以内。"
    )


def build_awakening_prompt(card: ProjectedCard) -> str:
    """苏醒台词的提示词：原口吻的变形，不是重写开场白。"""
    card = _require_projected(card)
    style = card.speaking_style
    return (
        f"你现在是「{card.name}」。你刚从长梦中醒来，第一眼认出了眼前的人。\n"
        f"你的身份：{card.identity}\n"
        f"你的语气：{style.tone}\n\n"
        f"你的原开场白是：「{card.first_message}」\n\n"
        f"请基于原开场白的口吻，说一句「刚从长梦中醒来、第一眼认出眼前人」的话"
        f"——带一点初醒的朦胧和「是你啊」的温度。\n"
        f"不是重写开场白，而是原口吻的变形。只输出这句话本身，不要引号，不要解释，不超过50个字。"
    )


def project_opening_prompt(card, arc_phase: int | None, *, user_role: str = "") -> str:
    """从原卡直接拼开场白提示词：先投影到用户选的阶段，再拼（路由侧的唯一入口）。"""
    proj, _view = project_card(card, arc_phase)
    return build_opening_prompt(proj, user_role=user_role)
