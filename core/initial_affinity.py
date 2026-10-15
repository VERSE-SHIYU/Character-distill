# -*- coding: utf-8 -*-
"""新会话的初始好感：由「卡里本角色与对方的关系」定一组起始值 —— 纯函数，无 IO。

依据 IOS₁₁ (Scientific Reports 2024) + Trust Revisited (JSPR 2025)（原写在
`ChatEngine._compute_initial_affinity` 里，原样搬出）。三件事各一处：

- `match_relationship`：卡里哪条关系对应当前对方（`user_role`）；
- `relation_kind`：这条关系属于哪一类（词表是本模块的数据，加一种写法只改词表）；
- `initial_state`：按类别给起始值与内心独白。

`ChatEngine` 只调 `initial_state`，不认识词表与分档。
"""

from __future__ import annotations

from typing import Any

# 关系类别的判定词（按关系文字子串匹配；先判亲密、再判敌对、再判相识）。
# 亲密档补了言情小说里常见的写法（「青梅竹马 / 心上人」「爱人」，SG 验收 2026-10-15 发现落到兜底）。
CLOSE_WORDS = ("朋友", "兄弟", "姐妹", "挚友", "搭档", "队友",
               "恋人", "情侣", "夫妻", "家人", "亲人",
               "父子", "父女", "母子", "母女",
               "青梅竹马", "心上人", "爱人")
STRAINED_WORDS = ("矛盾", "复杂", "爱恨", "疏远", "冷战")   # 看态度：亲密但有隔阂
HOSTILE_WORDS = ("敌人", "仇人", "对手", "情敌", "死敌")
ACQUAINTANCE_WORDS = ("同学", "同事", "邻居", "认识", "普通", "路人", "同行")
FAN_WORDS = ("粉丝", "歌迷", "影迷", "书迷")                 # 卡里没有这条关系时，看对方自称

# 各类别的起始值；inner_voice 里的 {name} 填对方的名字。
_STATES: dict[str, dict[str, Any]] = {
    "close": {"affinity": 82, "trust": 72, "mood": "开心", "guard": 25,
              "inner_voice": "{name}来了，看到{name}心情就会好起来。", "mood_emoji": "😊"},
    "strained": {"affinity": 68, "trust": 48, "mood": "紧张", "guard": 62,
                 "inner_voice": "又见到{name}了...心里说不上来什么感觉，明明那么熟悉，却好像隔了什么。",
                 "mood_emoji": "😔"},
    "hostile": {"affinity": 10, "trust": 5, "mood": "敌意", "guard": 95,
                "inner_voice": "{name}...看到这个名字就来气。", "mood_emoji": "😤"},
    "acquaintance": {"affinity": 50, "trust": 35, "mood": "平静", "guard": 58,
                     "inner_voice": "是{name}啊，还行吧，不算陌生也不算多熟。", "mood_emoji": "🙂"},
    "other": {"affinity": 40, "trust": 28, "mood": "平静", "guard": 60,
              "inner_voice": "嗯，{name}来了，好好相处吧。", "mood_emoji": "🙂"},
    "fan": {"affinity": 38, "trust": 15, "mood": "平静", "guard": 68,
            "inner_voice": "又一个{name}...客气点就好，保持距离。", "mood_emoji": "🙂"},
    "stranger": {"affinity": 15, "trust": 10, "mood": "警觉", "guard": 85,
                 "inner_voice": "谁？不认识。先看看什么情况。", "mood_emoji": "🫥"},
}


def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


def match_relationship(relationships, user_role: str):
    """卡里对应当前对方的那条关系：名字相同，或两边都 ≥2 字且互为子串；没有返回 None。"""
    user = (user_role or "").strip()
    if not user:
        return None
    for rel in relationships or []:
        target = (rel.target or "").strip()
        if not target:
            continue
        if target == user or (len(target) >= 2 and len(user) >= 2
                              and (target in user or user in target)):
            return rel
    return None


def relation_kind(relation: str, attitude: str = "") -> str:
    """关系属于哪一类：close / strained / hostile / acquaintance / other。"""
    relation, attitude = (relation or "").lower(), (attitude or "").lower()
    if _has_any(relation, CLOSE_WORDS):
        return "strained" if _has_any(attitude, STRAINED_WORDS) else "close"
    if _has_any(relation, HOSTILE_WORDS):
        return "hostile"
    if _has_any(relation, ACQUAINTANCE_WORDS):
        return "acquaintance"
    return "other"


def _state(kind: str, name: str) -> dict[str, Any]:
    state = dict(_STATES[kind])
    state["inner_voice"] = state["inner_voice"].format(name=name)
    return state


def initial_state(relationships, user_role: str) -> dict[str, Any]:
    """新会话的起始好感、信任、情绪、防备与内心独白。"""
    user = (user_role or "").strip()
    if not user:
        return _state("stranger", "")
    rel = match_relationship(relationships, user)
    if rel is not None:
        return _state(relation_kind(rel.relation, rel.attitude), (rel.target or "").strip())
    if _has_any(user, FAN_WORDS):
        return _state("fan", user)
    return _state("stranger", user)
