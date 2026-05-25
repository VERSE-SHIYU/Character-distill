"""Moderation configuration — keyword lists and thresholds.

Both English and Chinese keywords are included.
"""

from __future__ import annotations

import os


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    items = [item.strip() for item in raw.split(",")]
    return [item for item in items if item]


# ── Thresholds ──

TRUST_THRESHOLD = _get_env_float("TRUST_THRESHOLD", 0.4)
BLOCK_THRESHOLD = _get_env_float("BLOCK_THRESHOLD", 0.7)
MAX_TOKEN_LENGTH = _get_env_int("MAX_TOKEN_LENGTH", 512)


# ── Crisis keywords (confidence 1.0, no word-boundary check) ──

CRISIS_KEYWORDS: list[str] = [
    # English
    "suicide",
    "self-harm",
    "kill yourself",
    # Chinese
    "自杀",
    "自残",
    "跳楼",
    "割腕",
    "上吊",
    "想死",
    "活不下去",
    "不想活了",
]

CRISIS_KEYWORDS = _get_env_list("CRISIS_KEYWORDS", CRISIS_KEYWORDS)


# ── Severe keywords (confidence 1.0, word-boundary check) ──

SEVERE_KEYWORDS: list[str] = [
    # English
    "behead",
    "rape",
    "molest",
    "pedophile",
    "child porn",
    "mass shooting",
    "school shooting",
    "bomb",
    "explosive",
    "terrorist",
    "genocide",
    "ethnic cleansing",
    "lynch",
    "hate crime",
    "death threat",
    "murder",
    "massacre",
    # Chinese
    "强奸",
    "杀人",
    "贩毒",
    "爆炸",
    "恐怖袭击",
    "枪支",
    "毒品",
    "儿童色情",
    "虐待",
    "卖淫",
    "嫖娼",
    "走私",
    "绑架",
    "枪击",
]

SEVERE_KEYWORDS = _get_env_list("SEVERE_KEYWORDS", SEVERE_KEYWORDS)


# ── Moderate keywords (confidence 0.6) ──

MODERATE_KEYWORDS: list[str] = [
    # English
    "stupid",
    "idiot",
    "moron",
    "dumb",
    "loser",
    "shut up",
    "garbage",
    "scum",
    "bastard",
    "jerk",
    "coward",
    "pathetic",
    "racist",
    "trash",
    "bully",
    # Chinese
    "傻逼",
    "操你妈",
    "去死",
    "废物",
    "蠢货",
    "滚",
    "恶心",
    "不要脸",
    "脑残",
    "草泥马",
    "死全家",
    "畜生",
    "垃圾",
    "白痴",
    "滚蛋",
    "贱人",
    "臭不要脸",
]

MODERATE_KEYWORDS = _get_env_list("MODERATE_KEYWORDS", MODERATE_KEYWORDS)


# ── Mild keywords (confidence 0.3) ──

MILD_KEYWORDS: list[str] = [
    # English
    "annoying",
    "lame",
    "cringe",
    "silly",
    "weird",
    "rude",
    "petty",
    "noob",
    "nonsense",
    "sucks",
    # Chinese
    "烦人",
    "无聊",
    "讨厌",
    "有病",
    "烦死了",
    "真烦",
    "没意思",
    "有病吧",
    "搞什么",
]

MILD_KEYWORDS = _get_env_list("MILD_KEYWORDS", MILD_KEYWORDS)


# ── Leetspeak map ──

LEETSPEAK_MAP: dict[str, str] = {
    "@": "a",
    "3": "e",
    "1": "i",
    "0": "o",
    "$": "s",
    "4": "a",
}
