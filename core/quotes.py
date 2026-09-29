# -*- coding: utf-8 -*-
"""原文引语 —— 抽带说话人的对话候选句，核对一段文字是否逐字出自原文。

**纯计算，不 import 任何项目模块**（同 `core/roster_aggregate.py` 的做法）：本模块只认识
「原文是一段字符串、角色名是一串称呼」这个形状，不认识 LLM、不认识提示词、不认识卡片。

为什么单独一个模块：产品侧（按编号从原文挑选对话示例）与验收侧（核对卡片里的对话与
引文是否逐字出自原文）要用**同一套**判定。各写一份，验收判「逐字」的口径迟早和产品
保证的口径分家 —— 那时验收就再也证明不了产品。

判据的中心是「引导语」：原文的对话形态是「凤姐忙和刘姥姥摆手道：“……”」，说话人写在
紧贴引号的引导语里（公开版本实测：86% 的对话句有引导语）。抽取靠它召回候选，核对靠它
把引号内的台词与出处对上；**归属**则由模型读整段 `context` 判定 —— 引导语里的人名常是
宾语（「送入刘姥姥口中，因笑道」是凤姐说的），按子串判会错。
"""

from __future__ import annotations

import re
from typing import NamedTuple, Sequence

# 归一化：去空白与标点。卡片里的标点写法与原文不保证一致（全角半角、有无皆可能），
# 不归一的话同一句话会因为一个逗号之差被判查不到。**只此一处** —— 抽取与核对共用。
_DROP_CHARS = frozenset("，。！？、；：“”‘’「」『』（）《》…—" + ",.!?;:\"'()<>-")
# 异体字并字表：版本间互为用字差异的异体字当作同一个字（「著/着」）。只放有依据的字组，
# 不并近义字 —— 依据与口径见 `docs/specs/quote-variant-fold.md`（《異體字字典》A03506）。
_VARIANT_FOLD = str.maketrans({"著": "着"})


def normalize(s) -> str:
    """只留实义字符：去全部空白与标点。"""
    return "".join(ch for ch in str(s) if not ch.isspace() and ch not in _DROP_CHARS).translate(_VARIANT_FOLD)


_ELLIPSIS = re.compile(r"…+|\.{3,}")


def verbatim_in_normalized(source_norm: str, quote: str) -> bool:
    """`quote` 是否逐字出现在**已归一化**的 `source_norm` 里（省略处允许跳过）。

    与 `verbatim_in` 的分工：核一张卡片要拿整本原文比几十条引文，`verbatim_in` 每条都会
    重新归一化一遍整段 source（80 万字 × 几十条），这一层只接受调用方归一化好的 source，
    把「整本归一化一次」的成本省下来。节选仍按省略号拆开逐段核对。
    """
    segs = [s for s in (normalize(p) for p in _ELLIPSIS.split(str(quote))) if s]
    return bool(segs) and all(s in source_norm for s in segs)


def verbatim_in(source: str, quote: str) -> bool:
    """`quote` 是否逐字出现在 `source` 里（归一化后比对，省略处允许跳过）。

    写成「甲……乙」的节选按省略号拆开逐段核对：整串一定查不到（省掉的那些话在原文里
    还在），但两段各自是原文中连着的话 —— 这是合法的引用形态，不拆会把每一处节选都
    判成编造。

    没有任何实义字符的 quote（空串、「……」）返回 False：无可核对的东西不算「找到了」。
    """
    return verbatim_in_normalized(normalize(source), quote)


# 卡片里成对的引文（角色自述、他人评价）—— **与抽对话的 `_QUOTE_PAIRS` 用途不同**：
# 那份按「一份文本只用出现最多的那一对」抽对话候选，这份是逐对找出卡片里所有被引号括住
# 的片段来做逐字核对。故英文双引号 `"` 也收进来（卡片里大量用它，此前默认配对表不含它，
# 引文一条都没查过），且六对各自独立匹配、互不排除 —— 嵌套的引语各算一条。
CITATION_PAIRS = (('"', '"'), ("'", "'"), ("‘", "’"), ("“", "”"), ("「", "」"), ("『", "』"))

# 归一化后不足这么长的引文太短、子串命中没有分辨力（「你老」满篇都是）——抽取时就丢掉，
# 调用方（卡片核对、验收）不必各自再判一次。
CITATION_MIN_CHARS = 4


def quoted_spans(text: str) -> list[tuple[int, int, str]]:
    """`text` 里每一条成对引文：`(起, 止, 引号内文字)`，起止含引号本身，按先后排序。

    只返回归一化后 ≥ `CITATION_MIN_CHARS` 的（更短的无分辨力，见上）。嵌套的引语各算一条
    （`“他说"好"。”` 里外层与内层都在），故六对独立匹配、不做互相排除。
    """
    spans: list[tuple[int, int, str]] = []
    for lq, rq in CITATION_PAIRS:
        pattern = re.escape(lq) + "([^" + re.escape(lq) + re.escape(rq) + "]+)" + re.escape(rq)
        for m in re.finditer(pattern, str(text)):
            if len(normalize(m.group(1))) < CITATION_MIN_CHARS:
                continue
            spans.append((m.start(), m.end(), m.group(1)))
    spans.sort()
    return spans


# 原文的对话引号样式：不同版本的书用不同的一对，同一本书里外层对话与内层引语用的是不同
# 的对。**一份文本只用出现最多的那一对**（按开引号计数，并列按下面的顺序取）—— 三对混着
# 匹配会把引语中的引语（`「他说『好』」`）当成另一句对话，于是后面每一句的「上一句」都
# 错位。三种都没有（如纯 ASCII 直引号）时不猜：抽不出候选（约束 12，本轮不支持）。
_QUOTE_PAIRS = (("“", "”"), ("「", "」"), ("『", "』"))
_LEAD_BREAK = re.compile(r"[。！？\n]")       # 引导语只取紧贴引号的那一句


def _quote_re(text: str) -> "re.Pattern[str] | None":
    """这份文本的主引号样式对应的正则；三种都没有时 None。"""
    best: tuple[int, str, str] | None = None
    for left, right in _QUOTE_PAIRS:
        n = text.count(left)
        if n and (best is None or n > best[0]):
            best = (n, left, right)
    if best is None:
        return None
    _n, left, right = best
    return re.compile(re.escape(left) + "([^" + re.escape(left)
                      + re.escape(right) + "]*)" + re.escape(right))


# 片段里上一句之前最多再带这么长的叙述：一段长叙述整段塞进去，是几倍的成本与注意力。
_CONTEXT_BACK = 60


class Candidate(NamedTuple):
    """一条候选：本角色的那句（`lead`/`line`）、紧邻的上一句（`prev_*`）与原文片段。

    `context` 是原文的**连续子串**，说话人由模型读它判定（见 `render_candidates`）。
    """

    n: int
    lead: str
    line: str
    prev_lead: str
    prev_line: str
    context: str


def extract_candidates(text: str, names: Sequence[str]) -> list[Candidate]:
    """抽出引导语里出现 `names` 中任一人名的对话句，按出场顺序编号（1 起）。

    引导语取引号前**最后一个句读之后**的那一截，不取整段叙述：一段叙述里可能先提了
    别人（「贾母听了，也笑了。宝玉道：“……”」），整段看会把人错记成贾母。

    引导语含人名即收 —— 含两个人名的引导语（约 15%：「凤姐忙和刘姥姥摆手道：」，说话
    的是凤姐）会因此多收一条错归属的候选。名字还常是宾语（「送入刘姥姥口中，因笑道」
    是凤姐说的），所以**归属对不对不靠这段子串判定**：模型读 `context` 整段确认后再挑。

    无引导语的裸引号（约 14%）归不到人，不进候选。全文第一句没有上一句、成不了对，也
    不进候选。每项带上上一句与原文片段 —— 对话示例要成对呈现，模型得看见对方那句与它
    前后的叙述，才判得出这一问一答算不算「体现角色说话风格的交互」。
    """
    wanted = [n for n in names if n]
    quote_re = _quote_re(text)
    if quote_re is None:
        return []
    quotes = list(quote_re.finditer(text))
    out: list[Candidate] = []
    for i, m in enumerate(quotes[1:], start=1):        # 第一句没有上一句
        lead, _ = _lead_before(text, quotes, i, m.start())
        if not any(name in lead for name in wanted):
            continue
        prev = quotes[i - 1]
        prev_lead, prev_start = _lead_before(text, quotes, i - 1, prev.start())
        # 片段从上一句开头往前 60 字、与再上一句结尾里较后处起，到本句收尾引号止。
        start = max(prev_start - _CONTEXT_BACK,
                    quotes[i - 2].end() if i >= 2 else 0)
        out.append(Candidate(n=len(out) + 1, lead=lead, line=m.group(1),
                             prev_lead=prev_lead, prev_line=prev.group(1),
                             context=text[start:m.end()]))
    return out


def _lead_before(text: str, quotes: list, i: int, start: int) -> tuple[str, int]:
    """第 i 个引号之前、离它最近的那截引导语，以及它在原文里的起点。

    起点返回的是**切掉前后空白之前**的位置：片段按下限截取时用它算「往前 60 字」。
    """
    gap_start = quotes[i - 1].end() if i else 0
    head = text[gap_start:start]
    cut = gap_start
    for mb in _LEAD_BREAK.finditer(head):
        cut = gap_start + mb.end()
    return head[cut - gap_start:].strip(), cut


def render_candidates(candidates: Sequence[Candidate]) -> str:
    """把候选渲染成模型要读的编号块 —— 编号就是这个 `n`，调用方按同一个 `n` 取原文。

    提示词里的编号与「按编号复制」的取数共用这份结构，不另写一份带编号的文本：两份
    编号一旦错位，挑的是这句、复制的是那句，而且验收看不出来（两句都是原文里的）。
    """
    blocks = []
    for c in candidates:
        blocks.append(f"[{c.n}] 片段：{c.context}\n"
                      f"    上一句：{c.prev_lead}“{c.prev_line}”\n"
                      f"    本句：{c.lead}“{c.line}”")
    return "\n".join(blocks)


# 一组示例两行（对方一句、角色一句），3 组够看出说话风格；再多只是把卡片撑长。
# 上限只能靠**字段数固定**来保证：strict 的 Schema 不支持 array 的 maxItems（约束 6），
# 服务端拦不住数组长度，所以挑选结果不是「变长的数组」而是 MAX_EXAMPLES 个固定槽位。
MAX_EXAMPLES = 3

# 上一句说话人判不准时的取值：模型选它、以及代码把它当不合格丢掉，用的是同一个字面量。
UNDECIDED = "无法判断"


def pick_slot(i: int) -> tuple[str, str]:
    """第 i 个挑选槽位的两个字段名（编号 / 上一句说话人）。

    槽位的数量与命名只由 `MAX_EXAMPLES` 生成：strict 的 schema 与 `valid_picks` 的校验共用
    这套名字 —— 两边一旦错开，就成了「服务端填 A 格、代码读 B 格」，而取到的仍是原文里的
    句子，验收看不出来。
    """
    return f"pick{i}", f"speaker{i}"


def pick_slot_properties(total: int, enum: Sequence[str]) -> dict[str, dict]:
    """strict schema 里挑选槽位的 properties：MAX_EXAMPLES 个「编号 + 说话人」字段。

    限量靠字段数固定（见 `MAX_EXAMPLES`）；编号 `0` 表示这一格不挑（`valid_picks` 丢掉）。
    说话人（对方名）与编号同在一个槽位，配对由结构保证：模型读片段后从 `enum` 里选。
    """
    props: dict[str, dict] = {}
    for i in range(1, MAX_EXAMPLES + 1):
        n_key, speaker_key = pick_slot(i)
        props[n_key] = {"type": "integer", "minimum": 0, "maximum": total}
        props[speaker_key] = {"type": "string", "enum": list(enum)}
    return props


def valid_picks(raw, total: int, enum: Sequence[str]) -> list[tuple[int, str]]:
    """留下 `raw` 里合法的挑选项，返回 `(编号, 上一句说话人)`，顺序即槽位顺序。

    合格 = 编号是整数、在 1..total 内、未重复，且说话人在 `enum` 里又并非 `UNDECIDED`；
    编号 `0`（该格不挑）、缺字段、类型不对、越界、重复、判不准 —— 一律丢。非 strict 供应商
    可能给不合法 JSON 或编造参数（约束 6），越界编号直接索引会抛 KeyError —— 调用方分不清
    是挑选失败（预期内，该报任务失败）还是代码错误。故这里只挑出能用的，一个都没有时返回
    空列表，由调用方判「没有可用的挑选结果」。
    """
    if not isinstance(raw, dict):
        return []
    allowed = set(enum) - {UNDECIDED}
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for i in range(1, MAX_EXAMPLES + 1):
        n_key, speaker_key = pick_slot(i)
        n, speaker = raw.get(n_key), raw.get(speaker_key)
        if isinstance(n, bool) or not isinstance(n, int):
            continue
        if not 1 <= n <= total or n in seen or speaker not in allowed:
            continue
        seen.add(n)
        out.append((n, speaker))
    return out


def build_example(candidate: Candidate, name: str, prev_speaker: str) -> str:
    """一组示例：`对方名：上一句` + `角色名：本句` —— 两行都是候选里的原文，一字不改。

    对方的称呼是模型读片段后从名单 enum 里选的（`prev_speaker`），代码只负责拼装；判不准
    的那些在 `valid_picks` 就丢掉了，这里拿到的必是名单里的标准名。
    """
    return f"{prev_speaker}：{candidate.prev_line}\n{name}：{candidate.line}"
