"""本场记录：一个一对一存档里「现在是什么状态」（④ 段 1，spec `docs/specs/session-record.md`、
`docs/specs/session-record-extract.md`）。

三栏：角色知道的关于对方的事 / 角色和对方关系的变化 / 发生过的事和约定。每轮由**单独一次**抽取调用
更新（不和好感评估共用一次调用），有效条目整份注入提示词动态区；不显示给用户。

这一个模块管本场记录的全部规则（和 `core/affinity_protocol.py` 管好感协议同一个写法）：
栏的定义、抽取提示词、校验、应用、注入块、序列化。别处只调这里，不各写一份。
本模块不做 IO：抽取用的模型调用由调用方以 ``call`` 传进 `extract`。

**模型输出是信任边界**：条目的来源（用户说的 / 角色说的）不信模型，由代码看依据原话出现在
这一轮哪一边来定；「取代」的编号也由代码核对。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from core.llm_json import loads_llm_json
from core.quotes import verbatim_in
from core.tokens import count_tokens

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Column:
    """一栏。``label`` 里的 ``{who}`` 是「谁知道 / 谁和对方」：抽取时填角色名（第三人称），
    注入给角色时填「你」。栏名只在这里写一次。"""
    key: str
    label: str
    definition: str


# 顺序就是注入块里的顺序。定义的出处见 spec `session-record-extract.md` §3。
COLUMNS: tuple[Column, ...] = (
    Column("known", "{who}知道的关于对方的事",
           "对方自己说出的、关于对方本人的事：身份、经历、喜好、身边的人。依据只能是对方的原话。"),
    Column("relation", "{who}和对方关系的变化",
           "只在这一轮改变了两人之间的义务、冲突、相互期待或以后能否联系上时才记"
           "（原文：a recurring person mention becomes a relationship only when it changes "
           "obligations, conflicts, mutual expectations, or later reachability）。"),
    Column("events", "发生过的事和约定",
           "两人之间发生过的事，以及任何一方做出的承诺、计划或决定。"),
)
_COLUMN_KEYS = frozenset(c.key for c in COLUMNS)

SOURCE_USER, SOURCE_CHARACTER = "user", "character"
FIELD_ADD, FIELD_REPLACE = "record_add", "record_replace"

# 抽取调用的采样温度：PHASE-Tree 的状态抽取用 0.0（update_session_moment.py:281）。
EXTRACT_TEMPERATURE = 0.0
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


# ═══ 抽取提示词（单独一次调用；规则逐句出处见 spec §3）══════════════════════════

_EXTRACT_SYSTEM = """\
You are a character state analyst. After each conversation turn, you update the tracked state \
of one save between the character 「{char}」 and the user, who is referred to as 「对方」.

## CRITICAL RULES
1. **USE ONLY DIALOGUE EVIDENCE.** Every entry must be grounded in something explicitly said in \
the latest turn. Do NOT fabricate events or emotions not supported by the text. Each entry \
carries "evidence": an excerpt copied verbatim from the latest turn.
2. **THIRD-PERSON PERSPECTIVE.** You MUST write ALL entries in the THIRD PERSON: call the \
character 「{char}」 and the user 「对方」. Never use 「我」 or 「你」.
3. Only create entries for information RELEVANT to future decisions.
4. Do NOT duplicate information already in an existing entry -- check the current state first.
5. Keep content concise -- one clear sentence per entry.
6. SUPERSEDE an existing entry when new information contradicts or updates it. Include the ID of \
the entry being superseded. Look ACTIVELY for supersession patterns: "actually", "instead", \
"no longer", "switched to", "cancelled", "replaced X with Y", "now Y not X" \
（中文如「改主意了」「不…了」「改成」「换成」「其实」「取消」）. An entry that has merely not \
been mentioned recently is NOT contradicted: do not supersede it.
7. NO CHANGE: If the turn contains no new state-relevant information, or if the state already \
captures everything, return empty lists.
8. **你必须用中文撰写所有输出内容。**

## Columns
{columns}

## Output format
Output ONLY a valid JSON object, no other text:
{{"{add}": [{{"col": "{keys}", "text": "...", "evidence": "..."}}],
 "{replace}": [{{"id": <ID of the entry being superseded>, "col": "{keys}", "text": "...", "evidence": "..."}}]}}
"""


def render_extraction_prompt(record: SessionRecord, *, char_name: str, user_message: str,
                             assistant_reply: str) -> tuple[str, str]:
    """抽取调用的 (system, user)。system 只随角色名变；user 是当前有效条目 + 这一轮。"""
    columns = "\n".join(
        f"- {c.key}「{c.label.format(who=char_name)}」：{c.definition}" for c in COLUMNS)
    system = _EXTRACT_SYSTEM.format(
        char=char_name, columns=columns, add=FIELD_ADD, replace=FIELD_REPLACE,
        keys="|".join(c.key for c in COLUMNS))
    current = "".join(
        f"#{e.id} [{e.col}] {e.text}\n" for e in record.active()) or "（还没有）\n"
    user = (f"## Current state\n{current}\n"
            f"## Latest turn\n对方：{user_message}\n{char_name}：{assistant_reply}\n")
    return system, user


def extract(record: SessionRecord, *, char_name: str, user_message: str, assistant_reply: str,
            call: Callable[[str, str], str]) -> tuple[SessionRecord, list[str]]:
    """一轮抽取：拼提示词 → ``call(system, user)`` 拿模型回复 → 解析 → `apply`。

    ``call`` 由调用方给（生产走评估流水线，重放脚本走自己的适配器），本模块不碰模型和存储。
    回复不是 JSON 对象时抛错，由调用方决定怎么兜（记录原样不动）。
    """
    system, user = render_extraction_prompt(
        record, char_name=char_name, user_message=user_message, assistant_reply=assistant_reply)
    data = loads_llm_json(call(system, user))
    if not isinstance(data, dict):
        raise TypeError(f"extraction reply is not a JSON object: {type(data).__name__}")
    return apply(record, data, user_message, assistant_reply)


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
    if col not in _COLUMN_KEYS:
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
    """把抽取回复里的操作应用到 `record` 上。纯函数，不做 IO。"""
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


# ═══ 注入：提示词动态区的一块（给角色看，「你」= 角色）══════════════════════════

def render_block(record: SessionRecord) -> str:
    """有效条目整份注入；空记录返回 ""。某栏过长只打 warning，不截（R3）。"""
    active = record.active()
    if not active:
        return ""
    parts = [f"\n\n{BLOCK_TITLE}\n"]
    for col in COLUMNS:
        rows = [e.text for e in active if e.col == col.key]
        if not rows:
            continue
        body = " → ".join(rows) if col.key == "relation" else "\n".join(f"- {t}" for t in rows)
        if count_tokens(body) > COLUMN_TOKEN_WARN:
            logger.warning("Session record column %s is %d tokens (> %d)",
                           col.key, count_tokens(body), COLUMN_TOKEN_WARN)
        parts.append(f"{col.label.format(who='你')}：\n{body}\n")
    return "".join(parts)
