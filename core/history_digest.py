"""历史摘要块：历史窗口外的旧轮按块写成摘要（P13，spec `docs/specs/history-digest.md`）。

历史窗口按 token 预算从最新往前保留，更早的轮直接丢（`ChatEngine._build_llm_messages`）。
本模块把窗口外的轮从会话最开始**连续切成整块**（每块 `BLOCK_TURNS` 轮），每块写成一段第三人称
经过摘要；写完不再重写，总量设 token 上限，注入在历史窗口之前，让角色在长对话里接得上情节。

这一个模块管历史摘要块的全部规则（和 `core/session_record.py` 同一个写法）：块大小、提示词、
解析、注入块、序列化。别处只调这里，不各写一份。
本模块不做 IO、不认识 chat_engine / storage：写摘要用的模型调用由调用方以 ``call`` 传进 `extract`。

**为什么不重写**：LLM 反复重写同一段摘要会越写越差（SillyTavern-MessageSummarize 的理由）——
已写过的轮不再进 `pending_turns`，追加只发生在末尾。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.tokens import count_tokens

logger = logging.getLogger(__name__)

# 一块多少轮：MOOM 的打包粒度（spec §3.1）。
BLOCK_TURNS = 6
# 摘要总量上限：与本场记录注入块同量级（spec §9 决策三）。
BUDGET_TOKENS = 768
# 注入块的标题（对齐 session_record 的「【这个存档里发生过的事】」）。
BLOCK_TITLE = "【更早的经过】"
# 连续失败这么多次，本会话停止自动摘要（Claude Code 压缩的写法）。
MAX_CONSECUTIVE_FAILURES = 3


@dataclass(frozen=True)
class Digest:
    """一块摘要：`[from_turn, to_turn)` 轮（0 起、含头不含尾）压成的一段正文。"""
    from_turn: int
    to_turn: int
    text: str


@dataclass(frozen=True)
class Digests:
    """按块顺序排好的摘要序列；块从会话最开始连续切，故块边界由块数直接推出。"""
    items: tuple[Digest, ...] = field(default_factory=tuple)

    def covered_turns(self) -> int:
        """已摘要的轮数 —— 只切整块，故等于「块数 × 块大小」。"""
        return BLOCK_TURNS * len(self.items)

    # ── 序列化（存档行 `history_digest` 列的唯一读写格式）──
    def to_persist(self) -> str:
        return json.dumps(
            {"items": [d.__dict__ for d in self.items]}, ensure_ascii=False)

    @staticmethod
    def from_persist(raw: str | None) -> "Digests":
        """坏数据不抛：记一条 warning，当空（摘要丢了比聊天失败轻，同 SessionRecord）。"""
        if not raw:
            return Digests()
        try:
            rows = json.loads(raw)["items"]
            return Digests(tuple(Digest(**r) for r in rows))
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Corrupt history_digest, treating as empty: %r", exc)
            return Digests()


def pending_turns(history: list[dict[str, Any]], window_start: int,
                  digests: Digests) -> list[list[dict]]:
    """窗口外、尚未摘要的整轮，旧→新；每轮 = ``[user_msg, assistant_msg]``。

    ``window_start`` 是历史窗口起点的消息下标（轮边界、偶数），故窗口外的轮数是
    ``window_start // 2``；再减去已摘要的 ``covered_turns()``。
    """
    outside = window_start // 2
    return [[history[2 * t], history[2 * t + 1]]
            for t in range(digests.covered_turns(), outside)]


def next_block(pending: list[list[dict]]) -> list[list[dict]] | None:
    """满 ``BLOCK_TURNS`` 轮返回最旧那一块，不足返回 None（不调用）。"""
    if len(pending) < BLOCK_TURNS:
        return None
    return pending[:BLOCK_TURNS]


# stem 固定为 `You are a conversation summarizer`：目标检查按它给替身分流（同 session_record）。
_PROMPT_SYSTEM = """\
You are a conversation summarizer. 把一段多轮对话压缩成第三人称的经过摘要。

## 要求
1. **第三人称**：用角色名「{char}」称呼角色，用「对方」称呼用户；不要用「我」「你」。
2. 只记**发生过的事件、情绪变化和约定**，按时间顺序；不编造对话里没出现的事。
3. **不复述用户画像**（对方是谁、喜好、身份这类事实在别处单独记录）：聚焦这段对话里推进的情节。
4. 先写 <analysis> 草稿（简要梳理），再写 <summary> 正文（控制在 150 字以内）。
5. **用中文撰写所有输出内容。**

## 输出格式
<analysis>（简要梳理）</analysis>
<summary>（第三人称的经过摘要）</summary>
"""


def render_prompt(turns: list[list[dict]], char_name: str) -> tuple[str, str]:
    """一块（``BLOCK_TURNS`` 轮）的抽取调用 (system, user)。system 只随角色名变。"""
    lines: list[str] = []
    for user_msg, asst_msg in turns:
        lines.append(f"对方：{user_msg.get('content', '')}")
        lines.append(f"{char_name}：{asst_msg.get('content', '')}")
    system = _PROMPT_SYSTEM.format(char=char_name)
    user = "## 这段对话\n" + "\n".join(lines) + "\n"
    return system, user


_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S)


def parse(reply: str) -> str:
    """取 ``<summary>…</summary>`` 正文（`<analysis>` 草稿自然被排除）；缺正文抛 ValueError。"""
    m = _SUMMARY_RE.search(reply or "")
    text = m.group(1).strip() if m else ""
    if not text:
        raise ValueError(f"digest reply has no <summary> body: {(reply or '')[:200]!r}")
    return text


def render_block(digests: Digests, budget: int = BUDGET_TOKENS) -> str:
    """从新到旧取，累计超 ``budget`` 丢最旧；空 → ""。"""
    chosen: list[Digest] = []
    used = 0
    for d in reversed(digests.items):
        tok = count_tokens(d.text)
        if used + tok > budget:
            break
        chosen.append(d)
        used += tok
    if not chosen:
        return ""
    chosen.reverse()
    body = "\n".join(d.text for d in chosen)
    return f"\n\n{BLOCK_TITLE}\n{body}\n"


def extract(digests: Digests, turns: list[list[dict]], *, char_name: str,
            call: Callable[[str, str], str]) -> Digests:
    """拼提示词 → ``call(system, user)`` → `parse` → 追加一个 `Digest`。

    轮区间由 ``covered_turns()`` 推出：``[from, from+len(turns))``。
    调用失败 / 回复不可解析一律抛错，``digests`` 原样不动（由调用方决定怎么兜）。
    """
    system, user = render_prompt(turns, char_name)
    text = parse(call(system, user))
    start = digests.covered_turns()
    return Digests(digests.items + (Digest(from_turn=start, to_turn=start + len(turns), text=text),))
