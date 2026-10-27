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

import bisect
import re
import unicodedata
from typing import NamedTuple, Sequence

import opencc

# 繁转简（OpenCC 官方 `t2s`，Apache-2.0）：两侧先落回同一字形集再比。原文是繁体本、
# 卡片引文是模型转述的简体时，少这一步会把真引文判成编造、真口癖整条删。
# **模块级单例**：一张卡要跑几十条引文、整本原文上百万字，每次新建转换器是白烧内存与
# 时间（构造一次常驻约 +8MB）。跨线程共享：官方 `t2s.json` 是纯词典查表链、无可变状态
# （沙箱 8 线程 × 50 次并发共用同一实例转 6.4 万字，不一致 0 次）。
# 顺序在去标点**之前** —— `t2s` 按短语转换，先去标点会把跨标点的字粘成新短语。
_T2S = opencc.OpenCC("t2s")

# 归一化：去空白与标点。卡片里的标点写法与原文不保证一致（全角半角、有无皆可能），
# 不归一的话同一句话会因为一个逗号之差被判查不到。**只此一处** —— 抽取与核对共用。
_DROP_CHARS = frozenset("，。！？、；：“”‘’「」『』（）《》…—" + ",.!?;:\"'()<>-")
# 异体字并字表：版本间互为用字差异的异体字当作同一个字（「著/着」）。只放有依据的字组，
# 不并近义字 —— 依据与口径见 `docs/specs/quote-variant-fold.md`（《異體字字典》A03506）。
# 与 `t2s` 的分工：「著」不在 `t2s` 单字表里（`t2s` 只把「髮/發」并成「发」），故并字表仍要留。
_VARIANT_FOLD = str.maketrans({"著": "着"})


def normalize(s) -> str:
    """只留实义字符：繁转简后去全部空白与标点，再并异体字。

    **已知取舍**：`t2s` 会把不同的繁体字并成同一个简体字（「髮/發」都转「发」），这类
    差异在逐字核对里不再算差异 —— 卡片本身是简体，语义不变。反方向的边界（引文恰好
    切在一个整体转换的短语中间）只会退回今天的行为（判查不到），不会误判为命中。
    """
    folded = _T2S.convert(str(s))
    return "".join(ch for ch in folded if not ch.isspace() and ch not in _DROP_CHARS).translate(_VARIANT_FOLD)


def char_bigrams(s) -> list[str]:
    """规范化后的相邻两字 —— 「词」的唯一口径（诊断/BM25 共用，不当关卡）。"""
    n = normalize(s)
    return [n[i:i + 2] for i in range(len(n) - 1)]


def leading_ws(s: str) -> int:
    """前导空白长度：`s.strip()` 相对原串右移这么多（切分时给出片段起点用）。"""
    return len(s) - len(s.lstrip())


def normalized_starts(text: str, raw_starts: Sequence[int]) -> list[int]:
    """原文偏移 → 规范化坐标：即 `normalize(text[:r])` 的长度，按调用方原顺序返回。

    索引坐标靠的就是这条不变式（C6）：逐段规范化累加 == 规范化整段前缀长。**只此一处** ——
    场景索引与原文切片两类索引都调它，不各自再算一遍。

    两条路径：
    - 快路径：偏移排序后逐段累加 `len(normalize(text[prev:r]))`，再加尾段；总长 ==
      `len(normalize(text))` 才采用（同 `paragraph_bounds` 的不变式与写法）。代价与全文
      长度成正比，与偏移个数无关 —— 逐个算前缀时块数 ×7 建索引就 ×7。
    - 退回：总长对不上（`t2s` 跨边界并字等罕见情形）时逐个算 `len(normalize(text[:r]))`，
      宁可慢，不可用错坐标。
    """
    order = sorted(range(len(raw_starts)), key=lambda i: raw_starts[i])
    result = [0] * len(raw_starts)
    acc, prev = 0, 0
    for i in order:
        r = raw_starts[i]
        acc += len(normalize(text[prev:r]))
        prev = max(prev, r)
        result[i] = acc
    if acc + len(normalize(text[prev:])) == len(normalize(text)):
        return result
    return [len(normalize(text[:r])) for r in raw_starts]


_ELLIPSIS = re.compile(r"…+|\.{3,}")

# 省略号拼接核对的结构性下限（**只此一处**）。出处：oberst-lab/verifiable-by-construction
# `eval/citation/verbatim_criteria.json:63-68` —— `min_fragment_len=10`、`min_fragments=2`，
# 作者说明这两个数是 structural floors（防的是「碰巧也匹配得上」的短片段）；同一份判据另有
# 一条「没标出省略号的删减，相似度再高也判失败」。锚点与引文共用这两个数，不各自再定一份。
MIN_ELIDED_FRAGMENT_LEN = 10
MIN_ELIDED_FRAGMENTS = 2


def _segments(quote: str) -> list[str]:
    """按省略号切段后各段的规范化结果 —— 两个 locate 共用这一份切片。

    **切片在归一化之前**：`_DROP_CHARS` 含「…」，先归一化省略号就没了、切不动。这条不变式
    易错且两条 locate 都依赖，只写一处。
    """
    return [normalize(p) for p in _ELLIPSIS.split(str(quote))]


def locate_in_normalized(source_norm: str, quote: str) -> list[int] | None:
    """`quote` 各段都在 `source_norm` 里时返回**最长一段**的全部起点；任一段缺失返回 None。

    存在性判定与 `verbatim_in_normalized` 共用这一份分段与匹配（M14）：返回非 None 就是逐字
    对得上。**不新增顺序要求** —— 各段都在即通过，段序不管（C11）。

    位置只由最长一段定：省略号首段可能极短（「这……下回还清罢」的「这」在《孔乙己》全文出现
    22 次、首次在开篇），拿它定位置正好把要纠正的错放回阶段 1（C12），故取最长段。
    """
    segs = [s for s in _segments(quote) if s]
    if not segs or not all(s in source_norm for s in segs):
        return None
    longest = max(segs, key=len)
    return [m.start() for m in re.finditer(re.escape(longest), source_norm)]


def intervals_in_normalized(source_norm: str, text: str) -> list[tuple[int, int]]:
    """`text` 在 `source_norm` 里的全部连续出现区间（规范化坐标，半开）；找不到 → 空列表。

    与 `locate_in_normalized` 的分工：那条为「省略号拼接核对」设计，只取最长一段的起点；
    这条给「按位置判重叠」用，区间宽度就是被找文本自身，不会跨过中间的无关段落。原文里
    的省略号由 `normalize` 去掉，故含省略号的整段原文仍能对上。
    """
    n = normalize(text)
    if not n:
        return []
    return [(m.start(), m.start() + len(n)) for m in re.finditer(re.escape(n), source_norm)]


def _paragraph_index(bounds: list[int], pos: int) -> int:
    """规范化位置 `pos` 落在第几段（`bounds` = 各段起始偏移，按换行分段、升序）。"""
    lo, hi = 0, len(bounds)
    while lo < hi:                                # 二分：bounds 升序
        mid = (lo + hi) // 2
        if bounds[mid] <= pos:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def paragraph_bounds(source_text: str, source_norm: str) -> list[int] | None:
    """各段落起始在**规范化**原文里的偏移（按换行分段）；拼不回整段时 None。

    与 `_paragraph_index` 配对：这里建边界、那里查位置，坐标同源才算得对（只此一份）。
    逐段规范化长度累加 == 规范化整段长度（`normalized_starts` 记的那条不变式）；对不上
    （跨行短语被 `t2s` 并字等罕见情形）就不给边界 —— 宁可放宽，不可用错边界。
    """
    bounds: list[int] = []
    acc = 0
    for para in source_text.split("\n"):
        bounds.append(acc)
        acc += len(normalize(para))
    return bounds if acc == len(source_norm) else None


def raw_offsets(source_text: str, source_norm: str, positions: Sequence[int]) -> list[int | None]:
    """规范化坐标 → 原文坐标（`normalized_starts` 的反方向）；换不回的位置给 None。

    按段落换算：段落边界取 `paragraph_bounds`（同一条累加不变式），段内二分找第一个使
    `normalize(段[:j])` 长度超过段内偏移的 j。边界拼不回整段时全部 None —— 宁可不给，不给错的。
    只给日志与诊断用（整卡跳过时说明锚点在原文哪里），核对本身不走这条路。
    """
    bounds = paragraph_bounds(source_text, source_norm)
    if bounds is None:
        return [None for _ in positions]
    lines = source_text.split("\n")
    line_starts = [0]
    for line in lines[:-1]:
        line_starts.append(line_starts[-1] + len(line) + 1)
    out: list[int | None] = []
    for pos in positions:
        if not 0 <= pos < len(source_norm):
            out.append(None)
            continue
        i = _paragraph_index(bounds, pos)
        line, p = lines[i], pos - bounds[i]
        lo, hi = 0, len(line)
        while lo < hi:                            # 二分：前缀规范化长度随 j 单调不减
            mid = (lo + hi) // 2
            if len(normalize(line[:mid])) > p:
                hi = mid
            else:
                lo = mid + 1
        out.append(line_starts[i] + lo - 1)
    return out


def has_ellipsis(s) -> bool:
    """这段文字里有没有省略号（「……」或「...」）。"""
    return _ELLIPSIS.search(str(s)) is not None


def locate_elided_in_normalized(source_norm: str, quote: str,
                                paragraphs: list[int] | None = None) -> list[int] | None:
    """「甲……乙」式节选按省略号拼接核对，返**首段起点**；任一条不成立返 None。

    与 `locate_in_normalized` 的分工：那条给**引文**用（各段都在即通过、不要求顺序、取最长段
    定位置，C11/C12）；这条给**阶段锚点**用，判据更严 —— 锚点要标出一段连续的原文，省略号
    两边的片段必须**按顺序**出现、各自不短于 `MIN_ELIDED_FRAGMENT_LEN` 字、且落在**同一段落**
    （`paragraphs` 非 None 时按它判，即按换行分段）。段长按归一化后的字符数算。没标出省略号
    的删减只剩一段，不足 `MIN_ELIDED_FRAGMENTS` 段即 None —— 与「整串对不上」同一条出路，
    不引入任何相似度。
    """
    segs = [s for s in _segments(quote) if len(s) >= MIN_ELIDED_FRAGMENT_LEN]
    if len(segs) < MIN_ELIDED_FRAGMENTS:
        return None
    starts: list[int] = []
    at = 0
    for s in segs:                                # 从前往后依次找：后段必须落在前段之后
        m = re.compile(re.escape(s)).search(source_norm, at)   # S13：坐标模块不用 text.find
        if m is None:
            return None
        starts.append(m.start())
        at = m.end()
    if paragraphs is not None and len({_paragraph_index(paragraphs, x) for x in starts}) > 1:
        return None                               # 两段不在同一段落 → 不是一段连续引文
    return [starts[0]]


def locate_anchor(source_text: str, source_norm: str, anchor: str,
                  paragraphs: list[int] | None = None) -> list[int] | None:
    """阶段锚点在**规范化**原文里的位置；对不上返回 None。锚点定位的唯一入口。

    判定顺序照参考实现 oberst-lab/verifiable-by-construction `eval/citation/vccr.py`
    `_classify`（判据 `verbatim_criteria.json` v4 的 `tiers._order`：exact → normalized →
    elided，取第一个对得上的）：

    1. 逐字：锚点原样是 `source_text` 的子串 → 取首次出现，换算成规范化坐标。原文自带
       「……」的句子在这一档就通过（D7-a）。
    2. 不含省略号：规范化后整句比对（`locate_in_normalized`，行为与改前一致）。这一档包含
       第 1 档，故不含省略号的锚点不必先走第 1 档。
    3. 含省略号且逐字对不上：省略号是模型标出的删减，按 `locate_elided_in_normalized` 切段核对。

    含省略号的锚点不走第 2 档：本仓 `normalize` 会删掉「…」（`_DROP_CHARS`），而参考实现
    的规范化不删省略号；让它走第 2 档等于放过「标了省略号、实际没删」的锚点，与参考实现不同。
    """
    if not has_ellipsis(anchor):
        return locate_in_normalized(source_norm, anchor)
    m = re.compile(re.escape(anchor)).search(source_text)   # S13：坐标模块不用 text.find
    if m is not None:
        return normalized_starts(source_text, [m.start()])
    return locate_elided_in_normalized(source_norm, anchor, paragraphs)


def verbatim_in_normalized(source_norm: str, quote: str) -> bool:
    """`quote` 是否逐字出现在**已归一化**的 `source_norm` 里（省略处允许跳过）。

    与 `verbatim_in` 的分工：核一张卡片要拿整本原文比几十条引文，`verbatim_in` 每条都会
    重新归一化一遍整段 source（80 万字 × 几十条），这一层只接受调用方归一化好的 source，
    把「整本归一化一次」的成本省下来。节选仍按省略号拆开逐段核对 —— 判定与 `locate_in_normalized`
    同源：找得到位置就是对得上。
    """
    return locate_in_normalized(source_norm, quote) is not None


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
_SENTENCE_END = "。！？"
_LEAD_BREAK = re.compile(f"[{_SENTENCE_END}\n]")   # 引导语只取紧贴引号的那一句
_NARRATION_SENTENCE = re.compile(f"[{_SENTENCE_END}]")
# 引导语以冒号、逗号收尾 = 话还没说完。写的是 `_same_width` 折叠后的字形：全角半角都算。
_UNFINISHED = (":", ",")
# 两句之间的叙述超过这么多句，就不是一问一答（中间隔了别的事）。
MAX_NARRATION_BETWEEN = 2


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


# 片段里上一句之前再带这么多句原文（叙述、别人的台词都算）：上一句写的是「他说」或没有
# 署名时，「他」是谁写在更前面，模型得看得见才判得出。10 句取自中文小说说话人识别用的窗口
# （Chen et al., Findings of EMNLP 2023：前后各 10 句）；依据与测量见
# `docs/specs/dialogue-context-window.md`。
_CONTEXT_SENTENCES_BACK = 10
# 这 10 句最长带这么多字，再长就从这里截。不用「。！？」断句的文本数不到句末，不封顶会
# 把全文开头到上一句整段带上（14.6 万字的文本实测：一条片段 14.6 万字）；一句很长的台词
# 也一样。2000 字取自公版四大名著的实测：10 句超过它的只出现在长台词多的《红楼梦》（贾宝玉
# 1730 条候选里 23 条），《西游记》《三国演义》《水浒传》各量一个主角都是 0 条。
_CONTEXT_CHARS_BACK = 2000


class Candidate(NamedTuple):
    """一条候选：本角色的那句（`lead`/`line`）、紧邻的上一句（`prev_*`）与原文片段。

    `context` 是原文的**连续子串**，说话人由模型读它判定（见 `render_candidates`）。
    `start` 是本句开引号在**原文**中的起点（抽取时记下，不回找）——按位置归阶段用。
    """

    n: int
    lead: str
    line: str
    prev_lead: str
    prev_line: str
    context: str
    start: int = 0


def is_spoken(inner: str) -> bool:
    """引号里的这段是不是一句台词：台词以标点或符号收尾（“可恶！”），叙述里带引号的词以
    字、字母或数字收尾（“庭训”“退一步想”）。

    判的是「最后一个字是不是字」，不列标点清单：清单总会漏（公版《水浒传》用「．．．」当
    省略号），漏掉的台词既进不了候选，也当不了下一句的上一句。
    """
    tail = inner.rstrip()
    return bool(tail) and not tail[-1].isalnum()


def _pairable(text: str, prev: "re.Match[str]", cur: "re.Match[str]") -> bool:
    """`prev` 能不能当 `cur` 的「上一句」：它得是台词，且两句之间的叙述不超过
    `MAX_NARRATION_BETWEEN` 句。"""
    between = text[prev.end():cur.start()]
    return (is_spoken(prev.group(1))
            and len(_NARRATION_SENTENCE.findall(between)) <= MAX_NARRATION_BETWEEN)


def _same_width(s: str) -> str:
    """全角与半角当同一个字：比名字（名单写「小D」、正文写「小Ｄ」）和比引导语的收尾标点
    （`_UNFINISHED`）都过它。（`normalize` 去标点用的 `_DROP_CHARS` 是另一条规则，不走这里。）"""
    return unicodedata.normalize("NFKC", s)


def _names_any(spans: Sequence[str], wanted: Sequence[str]) -> bool:
    folded = [_same_width(s) for s in spans]
    return any(name in span for name in wanted for span in folded)


def extract_candidates(text: str, names: Sequence[str]) -> list[Candidate]:
    """抽出署名里出现 `names` 中任一人名、且能与上一句成对的台词，按出场顺序编号（1 起）。

    一个引号成为候选，要同时满足三条 —— 每条规则都写在它自己的函数里，此处不重复：

    1. 本句是台词（`is_spoken`）：引号内不以字收尾；叙述里带引号的词（“退一步想”）不算。
    2. 与紧邻的前一个引号能成对（`_pairable`）：前一个是台词，且两句之间的叙述不超过
       `MAX_NARRATION_BETWEEN` 句。全文第一句没有上一句、成不了对，不编号也不进候选。
    3. 署名里出现本角色的名字或别名：署名取引号前的引导语（`_lead_before`）与引号后那半句
       （`_tail_after`）两处，名字两侧都按 `_same_width` 折成全角半角同一字形再比。

    引导语/署名里的人名常是宾语（「送入刘姥姥口中，因笑道」是凤姐说的），所以**归属对不对
    不靠这段子串判定**：模型读 `context` 整段确认后再挑（见 `render_candidates`）。片段起点
    由 `_context_start` 给出（上一句之前 `_CONTEXT_SENTENCES_BACK` 句、封顶 `_CONTEXT_CHARS_BACK`
    字），终点到本句署名那半句为止（`_tail_after`）—— 模型得看见引号后的署名才判得出是谁说的。
    """
    wanted = [_same_width(n) for n in names if n]
    quote_re = _quote_re(text)
    if quote_re is None:
        return []
    quotes = list(quote_re.finditer(text))
    sentence_ends = [s.end() for s in _NARRATION_SENTENCE.finditer(text)]
    out: list[Candidate] = []
    for i, m in enumerate(quotes[1:], start=1):        # 第一句没有上一句
        prev = quotes[i - 1]
        if not is_spoken(m.group(1)) or not _pairable(text, prev, m):
            continue
        lead, _ = _lead_before(text, quotes, i, m.start())
        tail, end = _tail_after(text, quotes, i)
        if not _names_any((lead, tail), wanted):
            continue
        prev_lead, prev_start = _lead_before(text, quotes, i - 1, prev.start())
        start = _context_start(text, quotes, sentence_ends, prev_start)
        out.append(Candidate(n=0, lead=lead, line=m.group(1),
                             prev_lead=prev_lead, prev_line=prev.group(1),
                             context=text[start:end], start=m.start()))
    return _numbered(out)


def _numbered(candidates: Sequence[Candidate]) -> list[Candidate]:
    """按给出的顺序从 1 起编号。编号只在这里定：给模型看的编号（`render_candidates`）、
    schema 里编号的上限、按编号取回原文，认的都是这同一个 `n`。"""
    return [c._replace(n=n) for n, c in enumerate(candidates, start=1)]


def _lead_before(text: str, quotes: list, i: int, start: int) -> tuple[str, int]:
    """第 i 个引号之前、离它最近的那截引导语，以及它在原文里的起点。

    引导语到上一个句读为止。换行也算断点，除非换行前那半句还没说完（以冒号、逗号收尾，
    如「闰土又对我说：⏎“……”」）—— 那是引导语和引号分在了两行。

    起点返回的是**切掉前后空白之前**的位置：`_context_start` 从它往前数句子。
    """
    gap_start = quotes[i - 1].end() if i else 0
    head = text[gap_start:start]
    body = head.rstrip()
    if not _same_width(body[-1:]).endswith(_UNFINISHED):
        body = head                      # 引导语已经说完：尾部的换行照常算断点
    cut = 0
    for mb in _LEAD_BREAK.finditer(body):
        cut = mb.end()
    return body[cut:].strip(), gap_start + cut


def _context_start(text: str, quotes: list, sentence_ends: Sequence[int], prev_start: int) -> int:
    """片段的起点：从上一句的引导语（`prev_start`）往前数 `_CONTEXT_SENTENCES_BACK` 句，
    最长 `_CONTEXT_CHARS_BACK` 字。

    数到的位置落在某句台词里面时，退到那句台词的引导语开头 —— 片段不从半句话开始，那句话
    是谁说的也一并带上。字数封顶在最后：超了就按字数截，这时片段可以从半句话开始。
    `sentence_ends` 是全文的句末位置（升序），由调用方算一次、各条候选共用：逐条重扫全文
    在长书上是平方级的。
    """
    k = bisect.bisect_right(sentence_ends, prev_start) - _CONTEXT_SENTENCES_BACK - 1
    start = sentence_ends[k] if k >= 0 else 0
    q = bisect.bisect_right(quotes, start, key=lambda m: m.start()) - 1
    if q >= 0 and start < quotes[q].end():
        _, start = _lead_before(text, quotes, q, quotes[q].start())
    return max(start, prev_start - _CONTEXT_CHARS_BACK)


def _tail_after(text: str, quotes: list, i: int) -> tuple[str, int]:
    """第 i 个引号之后的那半句署名（“……”四叔说。），以及片段该截到哪。

    只认在下一个引号之前被句读或换行收住的半句；直接接着下一个引号的（“……”孔乙己答道，
    “……”）是下一句的引导语，不算这一句的署名。片段带上这半句：句读算在内，换行不算。
    """
    m = quotes[i]
    nxt = quotes[i + 1].start() if i + 1 < len(quotes) else len(text)
    closed = _LEAD_BREAK.search(text, m.end(), nxt)
    if closed is None:
        return "", m.end()
    end = closed.start() if closed.group() == "\n" else closed.end()
    return text[m.end():closed.start()], end


# 交给模型挑选的候选最多这么多条。长书主角的候选上千条（公版《红楼梦》贾宝玉 1730 条），
# 每条带着片段全交出去是上百万 token，超过模型一次能读的量；挑 3 组示例也用不着。
MAX_CANDIDATES = 40


def spread_evenly(candidates: Sequence[Candidate]) -> list[Candidate]:
    """交给模型的候选：多于 `MAX_CANDIDATES` 条时按出场顺序均匀取这么多条（首尾都取）；
    不管取没取，都从 1 起重新编号 —— 递进来的候选带着什么编号，模型看到的都是 1 到 N。

    均匀取是为了全书各处都有：只取开头那些的话，示例全落在故事最前面。
    """
    if len(candidates) > MAX_CANDIDATES:
        last = len(candidates) - 1
        candidates = [candidates[i * last // (MAX_CANDIDATES - 1)] for i in range(MAX_CANDIDATES)]
    return _numbered(candidates)


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
# 服务端拦不住数组长度，所以挑选结果不是「变长的数组」而是 PICK_SLOTS 个固定槽位。
MAX_EXAMPLES = 3
# 格子比要的示例多一倍：模型如实标出「上一句是本人 / 名单外的人」的格子会被丢弃，
# 只给 MAX_EXAMPLES 格时这些格子会把名额占光（实测赵太爷 3 格全丢、0 组）。
PICK_SLOTS = 2 * MAX_EXAMPLES

# 上一句说话人不是名单里其他人时的取值：模型选它们、代码把它们当不合格丢掉，用同一批字面量。
UNDECIDED = "无法判断"
OUTSIDER = "名单外的人"


class SpeakerOptions(NamedTuple):
    """「上一句是谁说的」的选项，以及其中哪些算合格。

    schema 的 enum（`pick_slot_properties`）与校验（`valid_picks`）共用这一份：两边各拿一份
    名单的话，迟早出现「模型能选、代码不认」或反过来。合格的只有名单里的其他人；本人、
    名单外的人、无法判断是给模型如实作答的出口 —— 选项里没有正确答案时，它会从名单里
    硬挑一个，甚至返回非法 JSON。
    """

    others: tuple[str, ...]
    subject: str

    @property
    def enum(self) -> list[str]:
        return [*self.others, self.subject, OUTSIDER, UNDECIDED]

    def accepts(self, speaker: object) -> bool:
        return speaker in self.others


def pick_slot(i: int) -> tuple[str, str]:
    """第 i 个挑选槽位的两个字段名（编号 / 上一句说话人）。

    槽位的数量只由 `PICK_SLOTS` 生成：strict 的 schema 与 `valid_picks` 的校验共用
    这套名字 —— 两边一旦错开，就成了「服务端填 A 格、代码读 B 格」，而取到的仍是原文里的
    句子，验收看不出来。
    """
    return f"pick{i}", f"speaker{i}"


def pick_slot_properties(total: int, options: SpeakerOptions) -> dict[str, dict]:
    """strict schema 里挑选槽位的 properties：`PICK_SLOTS` 个「编号 + 说话人」字段。

    限量靠字段数固定（见 `PICK_SLOTS`）；编号 `0` 表示这一格不挑（`valid_picks` 丢掉）。
    说话人（对方名）与编号同在一个槽位，配对由结构保证：模型读片段后从 `enum` 里选。
    """
    props: dict[str, dict] = {}
    for i in range(1, PICK_SLOTS + 1):
        n_key, speaker_key = pick_slot(i)
        props[n_key] = {"type": "integer", "minimum": 0, "maximum": total}
        props[speaker_key] = {"type": "string", "enum": options.enum}
    return props


def valid_picks(raw, total: int, options: SpeakerOptions) -> list[tuple[int, str]]:
    """留下 `raw` 里合法的挑选项，返回 `(编号, 上一句说话人)`，顺序即槽位顺序。

    合格 = 编号是整数、在 1..total 内、未重复，且说话人被 `options.accepts` 接受（名单里的
    其他人；本人、名单外的人、无法判断都不合格）。编号 `0`（该格不挑）、缺字段、类型不对、
    越界、重复、判不准 —— 一律丢。非 strict 供应商可能给不合法 JSON 或编造参数（约束 6），
    越界编号直接索引会抛 KeyError —— 调用方分不清是挑选失败（预期内，该报任务失败）还是
    代码错误。故这里只挑出能用的，一个都没有时返回空列表，由调用方判「没有可用的挑选结果」。
    槽位多于要的组数（`PICK_SLOTS > MAX_EXAMPLES`）：凑满 `MAX_EXAMPLES` 组即停。
    """
    if not isinstance(raw, dict):
        return []
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for i in range(1, PICK_SLOTS + 1):
        if len(out) == MAX_EXAMPLES:
            break
        n_key, speaker_key = pick_slot(i)
        n, speaker = raw.get(n_key), raw.get(speaker_key)
        if isinstance(n, bool) or not isinstance(n, int):
            continue
        if not 1 <= n <= total or n in seen or not options.accepts(speaker):
            continue
        seen.add(n)
        out.append((n, speaker))
    return out


def build_example(candidate: Candidate, name: str, prev_speaker: str) -> str:
    """一组示例：`对方名：上一句` + `角色名：本句` —— 两行都是候选里的原文，一字不改。

    对方的称呼是模型读片段后从名单里选的（`prev_speaker`），代码只负责拼装；不合格的
    （本人／名单外的人／无法判断）在 `valid_picks` 就丢掉了，这里拿到的必是名单里的标准名。
    """
    return f"{prev_speaker}：{candidate.prev_line}\n{name}：{candidate.line}"
