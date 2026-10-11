"""本场记录：一个一对一存档里「现在是什么状态」（④ 段 1，spec `docs/specs/session-record.md`）。

三栏：他对你的了解 / 你们关系的变化 / 发生过的事和承诺。每轮并进好感评估那一次调用抽一次，
有效条目整份注入提示词动态区；不显示给用户。

这一个模块管本场记录的全部规则（和 `core/affinity_protocol.py` 管好感协议同一个写法）：
给评估模型看的说明、JSON 字段、校验、应用、注入块、序列化。别处只调这里，不各写一份。

**模型输出是信任边界**：条目的来源（用户说的 / 角色说的）不信模型，由代码看依据原话出现在
这一轮哪一边来定；「取代」的编号也由代码核对。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any

from core.quotes import verbatim_in
from core.tokens import count_tokens

logger = logging.getLogger(__name__)

# 栏 → 给模型和提示词看的中文名。顺序就是注入块里的顺序。
COLUMNS: dict[str, str] = {
    "known": "他对你的了解",
    "relation": "你们关系的变化",
    "events": "发生过的事和承诺",
}
SOURCE_USER, SOURCE_CHARACTER = "user", "character"

FIELD_ADD, FIELD_REPLACE = "record_add", "record_replace"

# R3：某一栏超过这么多 token 只打 warning，不截断（动态区不进预算逻辑）。
COLUMN_TOKEN_WARN = 256
# 每轮输出保持短：一轮最多收这么多条操作，多出的丢掉并打 warning。
MAX_OPS_PER_TURN = 6

BLOCK_TITLE = "【这个存档里发生过的事】"


@dataclass(frozen=True)
class Entry:
    id: int
    col: str
    text: str
    evidence: str
    source: str
    active: bool = True
    replaces: int | None = None


@dataclass(frozen=True)
class SessionRecord:
    entries: tuple[Entry, ...] = field(default_factory=tuple)

    # ── 读 ──
    def active(self) -> list[Entry]:
        return [e for e in self.entries if e.active]

    def get(self, entry_id: Any) -> Entry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def _next_id(self) -> int:
        return max((e.id for e in self.entries), default=0) + 1

    # ── 序列化（存档行 `session_record` 列的唯一读写格式）──
    def to_persist(self) -> str:
        return json.dumps(
            {"entries": [e.__dict__ for e in self.entries]}, ensure_ascii=False)

    @staticmethod
    def from_persist(raw: str | None) -> "SessionRecord":
        """坏数据不抛：记一条 warning，当空记录（本场记录丢了比聊天失败轻）。"""
        if not raw:
            return SessionRecord()
        try:
            rows = json.loads(raw)["entries"]
            return SessionRecord(tuple(Entry(**r) for r in rows))
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Corrupt session_record, treating as empty: %r", exc)
            return SessionRecord()


# ═══ 评估提示词：说明 + 当前有效条目 + JSON 字段 ═══════════════════════════════

def render_rules(record: SessionRecord) -> str:
    """评估 prompt 里讲「怎么更新本场记录」的一段，附当前有效条目（带编号）。"""
    cols = "；".join(f"{k}={v}" for k, v in COLUMNS.items())
    current = "".join(
        f"  #{e.id} [{e.col}] {e.text}\n" for e in record.active()) or "  （还没有）\n"
    return (
        "本场记录更新规则（用于 record_add / record_replace）：\n"
        f"- 三栏：{cols}\n"
        "- 只看这一轮的一问一答，只写对话里明确出现的事，不推测；没有新内容就两个都给空列表\n"
        "- 每条写完整，带上必要的前因（例：因为你没笑他，所以愿意多说家里的事），不要只留一个孤立的词\n"
        "- 每条必须附 evidence：这一轮对话里的原话片段，一字不改地摘出来\n"
        "- known 栏只记对方自己说的话里透露的信息，你自己的回复不能作为依据\n"
        "- relation 栏只记有对话依据的具体变化，不写「关系变好了」这类结论；"
        "好感高低和亲近、平常、冲突三档不在这里判\n"
        "- 只有这一轮对话明确说出与旧条目相反的内容（如改口）时，才用 record_replace 取代旧条目；"
        "只是最近没再提、好像不在意了，不算推翻，不要取代\n"
        f"当前有效条目：\n{current}\n"
    )


def render_json_fields() -> str:
    """评估 prompt 的 JSON 段里，本场记录的两行。"""
    cols = "|".join(COLUMNS)
    return (
        f'  "{FIELD_ADD}": [{{"col": "{cols}", "text": "条目", "evidence": "这一轮的原话片段"}}],\n'
        f'  "{FIELD_REPLACE}": [{{"id": 要取代的条目编号, "col": "{cols}", '
        '"text": "新条目", "evidence": "这一轮里推翻旧条目的原话片段"}],\n'
    )


# ═══ 应用：校验每一条操作，返回新记录 + warning ════════════════════════════════

def _source_of(evidence: str, user_message: str, assistant_reply: str) -> str | None:
    """依据原话出现在这一轮哪一边。两边都有时算用户（R5：冲突时以用户为准）。"""
    if not evidence or not evidence.strip():
        return None
    if verbatim_in(user_message, evidence):
        return SOURCE_USER
    if verbatim_in(assistant_reply, evidence):
        return SOURCE_CHARACTER
    return None


def _checked(op: Any, user_message: str, assistant_reply: str) -> tuple[dict | None, str]:
    """一条操作的公共校验：结构、栏、依据（来源由代码定）。不合格返回 (None, 原因)。"""
    if not isinstance(op, dict):
        return None, f"not an object: {op!r}"
    col, text, evidence = op.get("col"), op.get("text"), op.get("evidence")
    if col not in COLUMNS:
        return None, f"unknown column {col!r}"
    if not isinstance(text, str) or not text.strip():
        return None, "empty text"
    if not isinstance(evidence, str):
        return None, "missing evidence"
    source = _source_of(evidence, user_message, assistant_reply)
    if source is None:
        return None, f"evidence not verbatim in this turn: {evidence!r}"     # 必须有依据 / R1 / R4
    if col == "known" and source != SOURCE_USER:
        return None, f"known column needs the user's own words: {evidence!r}"  # R5
    return {"col": col, "text": text.strip(), "evidence": evidence, "source": source}, ""


def _ops(data: dict, key: str, warns: list[str]) -> list[Any]:
    raw = data.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        warns.append(f"{key} is not a list, ignored")
        return []
    return raw


def apply(record: SessionRecord, data: dict, user_message: str,
          assistant_reply: str) -> tuple[SessionRecord, list[str]]:
    """把评估回复里的本场记录操作应用到 `record` 上。纯函数，不做 IO。

    和好感部分各自校验：这里不读、不改好感字段；这里出的错只丢本场记录的这一条操作。
    """
    warns: list[str] = []
    adds, replaces = _ops(data, FIELD_ADD, warns), _ops(data, FIELD_REPLACE, warns)
    if len(adds) + len(replaces) > MAX_OPS_PER_TURN:
        warns.append(f"too many ops ({len(adds) + len(replaces)}), kept first {MAX_OPS_PER_TURN}")
        replaces = replaces[:MAX_OPS_PER_TURN]
        adds = adds[:MAX_OPS_PER_TURN - len(replaces)]

    entries = list(record.entries)
    next_id = record._next_id()

    def add(fields: dict, replaces_id: int | None = None) -> None:
        nonlocal next_id
        entries.append(Entry(id=next_id, replaces=replaces_id, **fields))
        next_id += 1

    for op in replaces:
        fields, why = _checked(op, user_message, assistant_reply)
        if fields is None:
            warns.append(f"replace dropped: {why}")
            continue
        # 在本轮已改过的列表上找：同一轮里第二次取代同一条时，它已经失效（R2）
        target = next((e for e in entries if e.id == op.get("id")), None)
        if target is None or not target.active:                             # R2
            warns.append(f"replace target {op.get('id')!r} missing or inactive, added instead")
            add(fields)
            continue
        if target.col != fields["col"] or target.col == "relation":
            # 关系一栏按先后记成轨迹，不覆盖；跨栏取代不成立 —— 都按新增
            warns.append(f"replace of #{target.id} ({target.col}) as {fields['col']} added instead")
            add(fields)
            continue
        entries[entries.index(target)] = replace(target, active=False)
        add(fields, replaces_id=target.id)

    for op in adds:
        fields, why = _checked(op, user_message, assistant_reply)
        if fields is None:
            warns.append(f"add dropped: {why}")
            continue
        add(fields)

    return SessionRecord(tuple(entries)), warns


# ═══ 注入：提示词动态区的一块 ══════════════════════════════════════════════════

def render_block(record: SessionRecord) -> str:
    """有效条目整份注入；空记录返回 ""。某栏过长只打 warning，不截（R3）。"""
    active = record.active()
    if not active:
        return ""
    parts = [f"\n\n{BLOCK_TITLE}\n"]
    for col, label in COLUMNS.items():
        rows = [e.text for e in active if e.col == col]
        if not rows:
            continue
        body = " → ".join(rows) if col == "relation" else "\n".join(f"- {t}" for t in rows)
        if count_tokens(body) > COLUMN_TOKEN_WARN:
            logger.warning("Session record column %s is %d tokens (> %d)",
                           col, count_tokens(body), COLUMN_TOKEN_WARN)
        parts.append(f"{label}：\n{body}\n")
    return "".join(parts)
