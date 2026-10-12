"""本场记录：一个一对一存档里「现在是什么状态」（④ 段 1，spec `docs/specs/session-record.md`、
`docs/specs/session-record-extract.md`）。

四栏：角色知道的关于对方的事 / 角色和对方关系的变化 / 对方的计划和请求 / 角色答应的事（栏名以 `COLUMNS` 为准）。每轮由**单独一次**抽取调用
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


SOURCE_USER, SOURCE_CHARACTER = "user", "character"


_USER_ONLY = frozenset({SOURCE_USER})
_CHARACTER_ONLY = frozenset({SOURCE_CHARACTER})
_EITHER = frozenset({SOURCE_USER, SOURCE_CHARACTER})

# 每种 ``sources`` 写给模型的那句话。来源规则代码这一侧由 `_checked` 照 ``sources`` 判，
# 提示词这一侧由这张表生成：改一栏收谁的话，只改 `COLUMNS` 里那一行的 ``sources``。
_EVIDENCE_RULE: dict[frozenset[str], str] = {
    _USER_ONLY: "依据只能是对方的原话。",
    _CHARACTER_ONLY: "依据只能是{who}的原话。",
    _EITHER: "",
}


@dataclass(frozen=True)
class Column:
    """一栏和它的全部规则；别处只读这里的字段，不按栏名写分支。

    - ``label`` 里的 ``{who}`` 是「谁知道 / 谁和对方」：抽取时填角色名（第三人称），注入给角色时填「你」。
    - ``definition``：写给模型的范围说明（出处原文在括号里），不含来源规则。
    - ``sources``：这一栏只收谁的话（依据出现在这一轮哪一边，由 `_source_of` 定）。
    - ``trajectory``：按先后记成轨迹——只追加、不被取代，注入时用「→」连成一串。
    """
    key: str
    label: str
    definition: str
    sources: frozenset[str]
    trajectory: bool = False

    def describe(self, who: str) -> str:
        """抽取提示词里这一栏的一行。"""
        return (f"- {self.key}「{self.label.format(who=who)}」："
                f"{self.definition.format(who=who)}{_EVIDENCE_RULE[self.sources].format(who=who)}")

    def render_rows(self, rows: list[str]) -> str:
        """注入块里这一栏的正文。"""
        return " → ".join(rows) if self.trajectory else "\n".join(f"- {t}" for t in rows)


# 顺序就是注入块里的顺序。定义的出处见 spec `session-record-extract.md` §3 和「第五轮修改」。
COLUMNS: tuple[Column, ...] = (
    Column("known", "{who}知道的关于对方的事",
           "对方明确说出的、关于对方本人的事实：身份、经历、身体状况、明说的喜好、身边的人；"
           "对方提的问题不是关于对方的事实。",
           _USER_ONLY),
    Column("relation", "{who}和对方关系的变化",
           "只在这一轮改变了两人之间的义务、冲突、相互期待或以后能否联系上时才记"
           "（原文：a recurring person mention becomes a relationship only when it changes "
           "obligations, conflicts, mutual expectations, or later reachability）。",
           _EITHER, trajectory=True),
    Column("requests", "对方的计划和请求",
           "对方自己说出的、还没兑现的计划、行程、安排，以及对方提的请求"
           "（原文：From USER statements: explicit requests）。"
           "{who}对对方的猜测和叮嘱不记。",
           _USER_ONLY),
    Column("promises", "{who}答应的事",
           "{who}自己做出的、还没兑现的承诺或决定"
           "（原文：Promises or decisions the character made）。"
           "{who}讲自己的经历、看法、习惯，或劝对方做什么，都不记。",
           _CHARACTER_ONLY),
)
_COLUMNS_BY_KEY = {c.key: c for c in COLUMNS}

# 输出只有一个列表：每条事实一项；要取代旧条目，就在这一项上带 ``supersedes``（旧条目编号）。
# 一件事只写一项（Mem0 §2.1：每条候选事实对应一个操作）。
FIELD_OPS, FIELD_SUPERSEDES = "record_ops", "supersedes"

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
carries "evidence": ONE continuous excerpt copied verbatim from ONE line of the latest turn \
(either 对方's line or 「{char}」's line). Never join pieces of different lines into one evidence.
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
captures everything, return an empty list.
8. **你必须用中文撰写所有输出内容。**

## Columns
{columns}

## Output format
Output ONLY a valid JSON object, no other text. One item per fact; a fact that supersedes an \
existing entry is ONE item carrying "{sup}":
{{"{ops}": [{{"col": "{keys}", "text": "...", "evidence": "...", "{sup}": <ID of the entry being superseded, or null>}}]}}

## Few shot examples
(Character 「老陈」 below is only an example, not part of the real conversation.)

Current state: （还没有）
对方：你平时喜欢喝什么茶？
老陈：龙井吧，清淡。
Output: {{"{ops}": []}}

Current state: （还没有）
对方：我在杭州读研究生，下个月要去北京面试。
老陈：那可得好好准备。
Output: {{"{ops}": [{{"col": "known", "text": "对方在杭州读研究生。", "evidence": "我在杭州读研究生", "{sup}": null}}, {{"col": "requests", "text": "对方下个月要去北京面试。", "evidence": "下个月要去北京面试", "{sup}": null}}]}}

Current state: #2 [requests] 对方下个月要去北京面试。
对方：面试改到上海了，不去北京了。
老陈：上海也好。
Output: {{"{ops}": [{{"col": "requests", "text": "对方下个月的面试改在上海，不去北京了。", "evidence": "面试改到上海了，不去北京了", "{sup}": 2}}]}}

Current state: （还没有）
对方：说好了，你教我钓鱼。
老陈：行，下周三来。
Output: {{"{ops}": [{{"col": "promises", "text": "老陈答应下周三教对方钓鱼。", "evidence": "行，下周三来", "{sup}": null}}]}}

Do not return anything from the custom few shot example prompts provided above.
"""


def render_extraction_prompt(record: SessionRecord, *, char_name: str, user_message: str,
                             assistant_reply: str) -> tuple[str, str]:
    """抽取调用的 (system, user)。system 只随角色名变；user 是当前有效条目 + 这一轮。"""
    columns = "\n".join(c.describe(char_name) for c in COLUMNS)
    system = _EXTRACT_SYSTEM.format(
        char=char_name, columns=columns, ops=FIELD_OPS, sup=FIELD_SUPERSEDES,
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
    column = _COLUMNS_BY_KEY.get(col)
    if column is None:
        return None, f"unknown column {col!r}"
    if not isinstance(text, str) or not text.strip():
        return None, "empty text"
    if not isinstance(evidence, str):
        return None, "missing evidence"
    source = _source_of(evidence, user_message, assistant_reply)
    if source is None:
        return None, f"evidence not verbatim in this turn: {evidence!r}"     # 必须有依据 / R1 / R4
    if source not in column.sources:                                      # R5：这一栏只收谁的话
        return None, f"{col} column does not take {source} words: {evidence!r}"
    return {"col": col, "text": text.strip(), "evidence": evidence, "source": source}, ""


def apply(record: SessionRecord, data: dict, user_message: str,
          assistant_reply: str) -> tuple[SessionRecord, list[str]]:
    """把抽取回复里的操作按顺序应用到 `record` 上。纯函数，不做 IO。

    每一项是一条事实：带 ``supersedes`` 就取代那条旧条目，不带就新增。
    """
    warns: list[str] = []
    ops = data.get(FIELD_OPS)
    if ops is None:
        ops = []
    if not isinstance(ops, list):
        warns.append(f"{FIELD_OPS} is not a list, ignored")
        ops = []
    if len(ops) > MAX_OPS_PER_TURN:
        warns.append(f"too many ops ({len(ops)}), kept first {MAX_OPS_PER_TURN}")
        ops = ops[:MAX_OPS_PER_TURN]

    entries = list(record.entries)
    next_id = record._next_id()

    def add(fields: dict, replaces_id: int | None = None) -> None:
        nonlocal next_id
        entries.append(Entry(id=next_id, replaces=replaces_id, **fields))
        next_id += 1

    for op in ops:
        fields, why = _checked(op, user_message, assistant_reply)
        if fields is None:
            warns.append(f"op dropped: {why}")
            continue
        target_id = op.get(FIELD_SUPERSEDES)
        if target_id is None:
            add(fields)
            continue
        # 在本轮已改过的列表上找：同一轮里第二次取代同一条时，它已经失效（R2）
        target = next((e for e in entries if e.id == target_id), None)
        if target is None or not target.active:                             # R2
            warns.append(f"supersede target {target_id!r} missing or inactive, added instead")
            add(fields)
            continue
        if target.col != fields["col"] or _COLUMNS_BY_KEY[target.col].trajectory:
            # 轨迹栏只追加，不覆盖；跨栏取代不成立 —— 都按新增
            warns.append(f"supersede of #{target.id} ({target.col}) as {fields['col']} added instead")
            add(fields)
            continue
        entries[entries.index(target)] = replace(target, active=False)
        add(fields, replaces_id=target.id)

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
        body = col.render_rows(rows)
        if count_tokens(body) > COLUMN_TOKEN_WARN:
            logger.warning("Session record column %s is %d tokens (> %d)",
                           col.key, count_tokens(body), COLUMN_TOKEN_WARN)
        parts.append(f"{col.label.format(who='你')}：\n{body}\n")
    return "".join(parts)
