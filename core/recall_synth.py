"""出题规则（CoverSynth 式 2）—— 纯函数、零 IO。

关卡顺序按 spec §5「先便宜后贵」：g_ground（本地、确定）→ A.5 措辞评审（软）→
A.6 核查。顺序不改变录取结果（式 2 是「与」），只省调用。A.4 不用（spec ⑮）。

模型的原始 JSON 由调用方传入；本模块不调模型、不读文件、不解析异常消息。
提示词照抄自论文附录（本地 `e2e/scratch/recall/prompts/`，不入库），只认登记哈希。
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from core.quotes import char_bigrams, intervals_in_normalized, normalized_starts
from core.recall_eval import fragment_problems

# 提示词身份的唯一出处：内容差一个字节就拒用（spec §10）。
PROMPT_SHA256: dict[str, str] = {
    "own_words_A5": "3a16aa0982370acbd5109db3db82c6b4ec5993c4838048bc257c197421e38280",
    "verifier_A6": "026d60838790eab847c2841b66c11017972abc8a206d65d8b980200fe901999c",
    "intent_factual_A3": "ae4684baa1ea63f31734b3c73baccf84bb9857d7d63665c22f1a8938b0a5f8ca",
}

# 每份模板允许出现的占位名（`render` 只认这几个，多给少给都报错）。
PLACEHOLDERS: dict[str, frozenset[str]] = {
    "own_words_A5": frozenset({"question", "quotes"}),
    "verifier_A6": frozenset({"intent_criterion", "intent_field", "question", "quotes"}),
    "intent_factual_A3": frozenset(),
    "draft": frozenset({"character", "title", "window", "feedback"}),
    "multiturn": frozenset({"character", "title", "question", "entity", "feedback"}),
}

# 问法规则（spec ㉑，10-27 人工抽查 20 道：场合不明 5、人称搞反 1、片段答不了 2）。A.6 照抄论文、不查这两项，
# 故放在起草侧。`YOU_RULE` 两份提示词逐字共用；场合规则只加在起草：多轮提示词只拿到原问句、
# 拿不到原文，要求交代场合会逼模型编造。
OCCASION_RULE = """\
Pin down the occasion: include enough of the situation (who was there, where, or
  what had just happened) that the question can only refer to the moment in this
  window, not to something that happens many times in the story."""

YOU_RULE = """\
"You" always means {character}. Every pronoun and kinship term the user says
  must be correct from {character}'s own point of view; if the story passage is
  another character speaking, do not turn their "I" or "my brother" into "you" or
  "your brother" for {character}."""

# 起草提示词（spec ⑱）：论文没给，我们自己写，内容即 §5 第 2 步的规则。
# 占位走同一个 `render`；不登记哈希，因为它属于本仓库而非论文附录。
DRAFT_PROMPT = """\
You write one recall question for a character-grounded chat system.

Character: {character}
Work: {title}

Here is a short window from the story in which {character} appears:
<<<
{window}
>>>

Write ONE question that a user chatting with {character} would naturally say to
them, in second person ("you"), asking about something {character} experienced,
said, or did. The question must be answerable from this window alone.
Write the question in the same language as the window.

Rules:
- The question must not mention the source text, chapter numbers, the novel, or
  its author.
- Do not copy wording from the answer passage into the question; paraphrase.
- The answer must be fully stated in `should`; do not ask for a reason,
  motive, feeling, or inner thought the passage does not state.
- `should` must be a declarative sentence copied from the passage; never copy
  a question into `should`.
- Ask about one thing only; do not combine several questions.
- """ + OCCASION_RULE + """
- """ + YOU_RULE + """
- The `should` passage alone must answer the question: if asking who said
  something, the speaker must be identifiable within the passage; if asking for a
  kind of thing (a charge, a reason, a name), the passage must be exactly that kind
  of thing.
- `should` must be a verbatim contiguous passage of 10-40 characters copied
  exactly from the window, without ellipsis, unique within the story.
- If the window contains nothing worth asking about, return skip.

Reply with JSON only, one of:
{"query": "<question>", "should": ["<verbatim passage>"]}
{"skip": true}
{feedback}"""

# 多轮改写提示词：我们自己的设计（未见文献这样做），不登记哈希，占位走同一个 `render`。
# 答案片段原样沿用 —— 命中按证据区间判定、与问法无关（`recall_eval.score_case`）。
MULTITURN_PROMPT = """\
You rewrite one recall question into a two-turn chat for a character-grounded chat system.

Character: {character}
Work: {title}

Original question (asked by a user chatting with {character}):
{question}

The question mentions {entity}. Split it into two user turns:
- turn1: the user first brings up {entity} in passing, in a natural chatty way. It must
  name {entity} and must not ask the question or hint at its answer.
- turn2: the user then asks about exactly the same thing as the original question, but
  refers to {entity} only by a pronoun or by omission. turn2 must not contain {entity}
  or any other name or nickname of {entity}.

Rules:
- Both turns are said to {character}, in second person ("you"), casual chat tone.
- """ + YOU_RULE + """
- Write both turns in the same language as the original question.
- Never mention the source text, chapter numbers, the novel, or its author.

Reply with JSON only:
{"turn1": "<first user turn>", "turn2": "<second user turn>"}
{feedback}"""

# 第 1 次起草 + 至多修 2 次；第 3 次仍不过硬关 → 丢弃（spec §5 第 7 条）。
MAX_ATTEMPTS = 3

# 表 4 的 Factual lookup 行包装成 A.6 的第 6 项与 JSON 字段（⑧：照抄行文，不留空）。
_INTENT_CRITERION = "6. intent_match: does the QUESTION realize this intent?"
_INTENT_FIELD = ' "intent_match": bool,'

# 占位名只匹配字母/数字/下划线；模板里的 JSON 花括号（`{"answerable": ...}`）不受影响。
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def check_prompt(name: str, text: str) -> None:
    """照抄的提示词只认登记哈希。``text`` 须按字节解码传入，不做换行转换。"""
    expected = PROMPT_SHA256.get(name)
    if expected is None:
        raise ValueError(f"未登记的提示词：{name}")
    got = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if got != expected:
        raise ValueError(f"提示词 {name} 与登记哈希不符：{got}")


def render(name: str, template: str, **values: str) -> str:
    """替换登记过的占位；模板里的 JSON 花括号原样保留（绝不能用 ``str.format``）。"""
    allowed = PLACEHOLDERS.get(name)
    if allowed is None:
        raise ValueError(f"未登记的提示词：{name}")
    found = set(_PLACEHOLDER.findall(template))
    unregistered = (found - allowed) | (set(values) - allowed)
    if unregistered:
        raise ValueError(f"{name} 有未登记的占位：{sorted(unregistered)}")
    if found != set(values):
        raise ValueError(
            f"{name} 占位与取值不一致：缺 {sorted(found - set(values))}，"
            f"多 {sorted(set(values) - found)}")
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def intent_slots(row: str) -> dict[str, str]:
    """表 4 的一行 → A.6 的两个意图占位（criterion 带行文，field 带 JSON 字段）。"""
    return {
        "intent_criterion": f"\n{_INTENT_CRITERION}\n{row.strip()}",
        "intent_field": _INTENT_FIELD,
    }


def evidence_ids(quotes: Iterable[str]) -> list[str]:
    """证据编号的唯一出处：``e1``…``eN``，既是提示词里的 ``[eN]``，也是 A.6 ``per_doc`` 的键。"""
    return [f"e{i}" for i, _ in enumerate(quotes, 1)]


def quotes_block(quotes: Iterable[str]) -> str:
    """A.5 / A.6 共用的证据格式：一行一条，编号取自 `evidence_ids`。"""
    quotes = list(quotes)
    return "\n".join(f"[{eid}] {q}" for eid, q in zip(evidence_ids(quotes), quotes))


def multiturn_entity(
    question: str,
    roster: Iterable[Mapping[str, Any]],
    character: str,
    name_overrides: Mapping[str, Sequence[str]],
) -> tuple[str, list[str], list[str]] | None:
    """多轮选题：问句里出现名单中「问答角色本人以外」的人的**本名** → ``(本名, 称呼, 查泄漏用名)``。

    选题只认本名，外加 ``name_overrides``（调用方传入找锚点用的同一张表，如林黛玉 → 黛玉）；
    别名不参与选题 —— 别名混有「小姐」「公主」和别人的名字（spec ⑲）。查泄漏用名 =
    称呼 + 全部别名，交给关 1 的 turn2 检查（宁严勿漏）。名单即 ``cached_characters`` 的行；
    地名、事件名没有可靠名单，不选。出现多人时取问句里最先出现的，同位置取较长的称呼。
    """
    best: tuple[int, int, str, list[str], list[str]] | None = None
    for person in roster:
        name = person.get("name")
        if not name or name == character:
            continue
        calls = list(dict.fromkeys([name, *name_overrides.get(name, ())]))
        for n in calls:
            pos = question.find(n)
            if pos >= 0 and (best is None or (pos, -len(n)) < (best[0], -best[1])):
                leak = list(dict.fromkeys(
                    x for x in [*calls, *(person.get("aliases") or [])] if x))
                best = (pos, len(n), name, calls, leak)
    return None if best is None else best[2:]


def multiturn_problems(
    turn1: str, turn2: str, call_names: Sequence[str], leak_names: Sequence[str],
) -> list[str]:
    """关 1（确定性）：turn1 须含本名或覆盖表里的称呼；turn2 不得出现本名与任何别名。"""
    problems: list[str] = []
    if not any(n in turn1 for n in call_names):
        problems.append(f"turn1 没有提到「{call_names[0]}」（可用称呼：{'、'.join(call_names)}）")
    leaked = [n for n in leak_names if n in turn2]
    if leaked:
        problems.append(f"turn2 不能出现「{'、'.join(leaked)}」，改用代词或省略")
    return problems


def multiturn_question(turn1: str, turn2: str) -> str:
    """关 2 填进 A.6 ``{question}`` 的文本。A.6 没有上文占位，上文按此约定拼在问题前。"""
    return f"上文：{turn1}\n问题：{turn2}"


def _name_positions(text: str, names: Iterable[str]) -> list[int]:
    positions: set[int] = set()
    for name in names:
        if not name:
            continue
        pos = text.find(name)
        while pos >= 0:
            positions.add(pos)
            pos = text.find(name, pos + 1)
    return sorted(positions)


def sample_anchors(
    text: str,
    names: Sequence[str],
    k: int = 30,
    half_width: int = 120,
    strata: int = 60,
) -> list[dict[str, Any]]:
    """全书均分 ``strata`` 段，每段至多一个锚点（段里没提到主角就空着，不向邻段借）。

    同一段里出现多次取最后一次 —— 段「有没有提到」只看出现过没有。
    返回 ``{"pos", "text", "nstart", "nend"}``：``pos`` 是原文里的起点（不是窗口起点），
    ``text`` 是 ``pos ± half_width`` 的原文切片，规范化区间由 ``normalized_starts`` 给出。
    """
    if k <= 0 or not text:
        return []
    positions = _name_positions(text, names)
    if not positions:
        return []

    size = len(text)
    by_stratum: dict[int, int] = {}
    for pos in positions:
        by_stratum[pos * strata // size] = pos

    picked = sorted(by_stratum)
    if len(picked) > k:
        if k == 1:
            picked = picked[:1]
        else:
            m = len(picked)
            picked = [picked[j * (m - 1) // (k - 1)] for j in range(k)]

    anchors: list[dict[str, Any]] = []
    for stratum in picked:
        pos = by_stratum[stratum]
        start, end = max(0, pos - half_width), min(size, pos + half_width)
        nstart, nend = normalized_starts(text, [start, end])
        anchors.append({
            "pos": pos,
            "text": text[start:end],
            "nstart": nstart,
            "nend": nend,
        })
    return anchors


def ground_problems(
    draft: Mapping[str, Any],
    anchor: Mapping[str, Any],
    source_norm: str,
) -> list[str]:
    """关 g_ground（硬）：片段规则与人工标注共用 ``fragment_problems``，另须落在锚点窗口内。"""
    case_id = draft.get("id", "?")
    fragments = list(draft.get("should") or [])
    if not fragments:
        return [f"{case_id}: 没有 should 片段"]
    problems: list[str] = []
    for i, piece in enumerate(fragments):
        found = fragment_problems(case_id, "should", i, piece, source_norm, None)
        problems.extend(found)
        if found:
            continue
        start, end = intervals_in_normalized(source_norm, piece)[0]
        if start < anchor["nstart"] or end > anchor["nend"]:
            problems.append(f"{case_id}: should[{i}] 不在锚点窗口内")
    return problems


def _bool_of(result: Any, key: str) -> bool | None:
    if not isinstance(result, Mapping):
        return None
    value = result.get(key)
    return value if isinstance(value, bool) else None


def critic_ok(critic: Any) -> bool | None:
    """A.5 的软关：``searchable`` 解析成功返回它，结构不对 → None（既非过也非不过）。"""
    return _bool_of(critic, "searchable")


def verify_ok(verdict: Any, evidence_ids: Iterable[str]) -> bool | None:
    """A.6 的通过条件（§3.4）：``necessary`` 只报不卡；键集合对不上视为未解析。"""
    if not isinstance(verdict, Mapping):
        return None
    for key in ("answerable", "sufficient", "leak", "intent_match"):
        if not isinstance(verdict.get(key), bool):
            return None
    per_doc = verdict.get("per_doc")
    if not isinstance(per_doc, Mapping) or set(per_doc) != set(evidence_ids):
        return None
    if not all(isinstance(v, bool) for v in per_doc.values()):
        return None
    necessary = verdict.get("necessary")
    if not isinstance(necessary, list) or not all(isinstance(v, bool) for v in necessary):
        return None
    if not isinstance(verdict.get("notes"), str):
        return None
    return (
        verdict["answerable"]
        and verdict["sufficient"]
        and all(per_doc.values())
        and not verdict["leak"]
        and verdict["intent_match"]
    )


def _advice(result: Any) -> list[str]:
    """退回修改时交给起草方的原话：A.5 给 ``suggestion``，A.6 只给 ``notes``，其余兜 ``reason``。"""
    if not isinstance(result, Mapping):
        return []
    for key in ("suggestion", "notes", "reason"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return [value]
    return []


def _result(
    case_id: Any,
    status: str,
    problems: list[str],
    attempt: int,
    *,
    gate: str | None = None,
    own_words_flag: bool = False,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "status": status,
        "gate": gate,
        "problems": problems,
        "own_words_flag": own_words_flag,
        "attempt": attempt,
    }


def _hard_fail(case_id: Any, gate: str, problems: list[str], attempt: int) -> dict[str, Any]:
    if attempt >= MAX_ATTEMPTS:
        return _result(case_id, "discarded", problems, attempt, gate=gate)
    return _result(case_id, f"{gate}_fail", problems, attempt, gate=gate)


def judge(
    draft: Mapping[str, Any],
    anchor: Mapping[str, Any],
    source_norm: str,
    *,
    critic: Any = None,
    verdict: Any = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """判一条草稿：硬关决定录取，A.5 只退回修改、第 3 次仍不过则录取并打标记。

    ``attempt`` 从 1 起；``*_unparsed`` 不算一次修复，也不按丢弃处理 —— 由调用方
    决定重调一次，仍解析不了再记 ``discarded:parse``。
    """
    case_id = draft.get("id")
    problems = ground_problems(draft, anchor, source_norm)
    if problems:
        return _hard_fail(case_id, "ground", problems, attempt)

    searchable = critic_ok(critic)
    if searchable is None:
        return _result(case_id, "critic_unparsed", ["A.5 结果解析失败"], attempt, gate="critic")

    own_words_flag = False
    if searchable:
        if attempt < MAX_ATTEMPTS:
            return _result(case_id, "own_words_repair", _advice(critic), attempt, gate="critic")
        own_words_flag = True

    verified = verify_ok(verdict, evidence_ids(draft.get("should") or []))
    if verified is None:
        return _result(case_id, "verify_unparsed", ["A.6 结果解析失败"], attempt, gate="verify")
    if not verified:
        return _hard_fail(case_id, "verify", _advice(verdict), attempt)

    return _result(case_id, "passed", [], attempt, own_words_flag=own_words_flag)


def ledger_problems(
    draft_ids: Iterable[str],
    anchor_ids: Iterable[str],
    discarded_ids: Iterable[str] = (),
) -> list[str]:
    """台账自检：草稿 id 必须来自锚点，已丢弃的不能重出；锚点空着是允许的（不许补题）。"""
    anchors = set(anchor_ids)
    discarded = set(discarded_ids)
    problems: list[str] = []
    for draft_id in draft_ids:
        if draft_id in discarded:
            problems.append(f"{draft_id}: 已丢弃的锚点不能重出")
        elif draft_id not in anchors:
            problems.append(f"{draft_id}: 不是锚点 id")
    return problems


def outcome_counts(results: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """按终态分桶：丢弃的按硬关分开，带标记的录取单列；修复 / 未解析不计。"""
    counts: Counter[str] = Counter()
    for result in results:
        status = result.get("status")
        if status == "discarded":
            counts[f"discarded:{result.get('gate')}"] += 1
        elif status == "passed":
            counts["passed_own_words_flag" if result.get("own_words_flag") else "passed"] += 1
    return dict(counts)


def lexical_overlap(question: str, quotes: Sequence[str] | str) -> float:
    """诊断用：问句二元组里有多少出现在答案片段里（分母 = 问句），不当关卡。"""
    query = char_bigrams(question)
    if not query:
        return 0.0
    passages = [quotes] if isinstance(quotes, str) else quotes
    seen: set[str] = set()
    for passage in passages:
        seen.update(char_bigrams(passage))
    return sum(1 for bigram in query if bigram in seen) / len(query)


def overlap_histogram(values: Iterable[float]) -> dict[str, int]:
    """0.1 一档；1.0 并入 0.9 档（十档覆盖 [0, 1]）。"""
    counts: Counter[str] = Counter()
    for value in values:
        bucket = min(int(value * 10), 9)
        counts[f"{bucket / 10:.1f}"] += 1
    return dict(counts)
