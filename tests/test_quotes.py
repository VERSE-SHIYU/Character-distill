# -*- coding: utf-8 -*-
"""`core/quotes.py` 的判据锁：谁被抽成候选、一段文字算不算逐字出自原文。

**为什么是纯函数用例。** 抽取与核对不碰 HTTP、不碰库也不碰模型，却是 WP17 的地基：
产品按编号从原文复制对话示例、验收按同一份判定核对成品卡，两边共用本模块。地基判错
只会在真跑时以「对话示例 0/3 命中」的形态出现，届时既贵又看不出是抽错了还是模型挑错了。

**变异。** 每条用例都配一个「少做一步就红」的反例：只认一种引号、三对混着匹配、引导语
取整段叙述而不是最后一个句读、片段不给上一句、首句也收、渲染编号改成 0 起。
"""
from __future__ import annotations

import pytest

from core.quotes import (
    CITATION_MIN_CHARS,
    MAX_EXAMPLES,
    OUTSIDER,
    PICK_SLOTS,
    UNDECIDED,
    SpeakerOptions,
    build_example,
    extract_candidates,
    is_spoken,
    normalize,
    quoted_spans,
    render_candidates,
    valid_picks,
    verbatim_in,
)

# 真实形态：整段的叙述 + 引导语 + 引号。《红楼梦》的对话一律「“……”」，说话人写在紧贴
# 引号的引导语里；这里刻意混入五种形态 —— 开头的裸引号、引导语含两个人名、引导语带（）
# 动作、无引导语的裸引号、以及「最后一个句读之后才是引导语」的多句叙述。
PARA = (
    "“老太太来了？”\n"
    "贾母听了，也笑了。凤姐忙和刘姥姥摆手道：“你老快别这样说，"
    "我不过是替你张罗张罗，你倒来挑我的不是。”\n"
    "刘姥姥笑道：“姑娘说的我心里熨帖，我哪敢挑姑娘的不是。”\n"
    "“这话怎么讲？”\n"
    "刘姥姥叹道：“阿弥陀佛！我这一辈子也没见过这样的排场。”\n"
    "凤姐（笑）道：“你老慢慢说，别急。”\n"
)


def test_candidates_carry_their_lead_the_previous_line_and_a_running_number():
    """候选按出场顺序编号；引导语只取最后一个句读之后那截；上一句连同它的引导语带上。

    变异：引导语取整段叙述（`贾母听了，也笑了。凤姐…`）→ 第 1 条的 lead 变长。
    """
    cands = extract_candidates(PARA, ["刘姥姥"])

    assert [c.n for c in cands] == [1, 2, 3], [(c.n, c.lead) for c in cands]
    assert cands[0].lead == "凤姐忙和刘姥姥摆手道："      # 含第二个人名，仍收（见下条）
    assert cands[0].line == "你老快别这样说，我不过是替你张罗张罗，你倒来挑我的不是。"
    assert (cands[0].prev_lead, cands[0].prev_line) == ("", "老太太来了？"), "开头那条裸引号"

    assert cands[1].lead == "刘姥姥笑道："
    assert cands[1].prev_lead == "凤姐忙和刘姥姥摆手道："
    assert cands[1].prev_line.startswith("你老快别这样说")

    # 裸引号「这话怎么讲？」不进候选（无引导语 → 归不到人），但它是下一句的上一句。
    assert cands[2].lead == "刘姥姥叹道："
    assert (cands[2].prev_lead, cands[2].prev_line) == ("", "这话怎么讲？")


def test_the_very_first_quote_is_not_a_candidate():
    """全文第一句没有上一句，成不了对 —— 连候选都不进（补充 1-第 2 步）。

    变异：恢复「首句也收、prev 留空」→ 候选多一条，断言红。
    """
    text = "刘姥姥笑道：“姑娘说得是。”\n凤姐道：“你老慢慢说。”\n"

    assert extract_candidates(text, ["刘姥姥"]) == []


@pytest.mark.parametrize("inner, spoken", [
    ("可恶！", True), ("然而……。", True), ("我真傻，真的，", True), ("你还不配……", True),
    ("既然如此．．．", True),            # 公版《水浒传》：全角点当省略号
    ("What?", True), ("好的~", True),
    ("退一步想", False), ("庭训", False), ("阿Ｑ", False), ("第3", False),
    ("", False), ("  ", False),
])
def test_a_quote_is_a_spoken_line_unless_it_ends_in_a_word_character(inner, spoken):
    """台词以标点或符号收尾；叙述里带引号的词以字、字母、数字收尾。不靠标点清单。

    变异：改回只认列出来的几种标点 → 没列到的（「．」）判成不是台词；恒真 → 后六条红。
    """
    assert is_spoken(inner) is spoken


def test_a_finished_line_before_the_quote_is_not_its_lead():
    """上一行已经收住（标题这类不带句读的一行），换行就是断点：那一行里的名字不是引导语。

    变异：`_lead_before` 不再把换行当断点 → 标题里的「刘姥姥」成了引导语，多出一条候选。
    """
    text = "凤姐道：“你老请坐。”\n刘姥姥进大观园\n“这园子真大。”\n"

    assert extract_candidates(text, ["刘姥姥"]) == []


@pytest.mark.parametrize("end", ["：", "，", ":", ","])
def test_a_lead_left_unfinished_before_a_newline_still_leads_its_quote(end):
    """引导语以冒号或逗号收尾 = 话没说完，后面的换行不是断点；全角半角都算。

    变异：收尾不过 `_same_width` 就比 → 全角的两条找不到候选；从 `_UNFINISHED` 里去掉一种 →
    那一种的全角半角两条都找不到。
    """
    text = f"凤姐道：“你老请坐。”\n刘姥姥笑道{end}\n“姑娘说得是。”\n"

    assert [(c.lead, c.line) for c in extract_candidates(text, ["刘姥姥"])] == [
        (f"刘姥姥笑道{end}", "姑娘说得是。")]


def test_a_candidate_carries_the_source_fragment_ending_at_its_own_line():
    """`context` 是原文的连续子串：从上一句之前起，到本句结尾止（含上一句与引导语）。

    模型据片段判「本句到底是不是我说的」「上一句是不是另一个人说的」—— 只给引导语的话
    它看不出名字是宾语（约束 ①：「送入刘姥姥口中，因笑道」是凤姐说的）。

    变异：片段改回只含引导语 → 第二条断言红，且第四条看不到上一句。
    """
    cands = extract_candidates(PARA, ["刘姥姥"])
    c = cands[1]                                    # 刘姥姥笑道那条，上一句是凤姐

    assert c.context in PARA, "片段必须是原文的连续子串，不是拼出来的"
    assert c.context.endswith(c.lead + "“" + c.line + "”"), "到本句结尾止"
    assert "你老快别这样说" in c.context, "上一句（凤姐那句）在片段里"
    assert PARA.index(c.context) <= PARA.index("凤姐忙和刘姥姥摆手道："), "从上一句之前起"


def test_the_fragment_keeps_the_previous_line_and_caps_narration_before_it_at_sixty():
    """片段必含上一句（模型得看对方怎么接的话），上一句之前最多带 60 字叙述，且不吞掉
    再上一句的台词：一段长叙述整段塞进去是几倍的成本与注意力。

    变异：起点取再上一句结尾（不设 60 字上限）→ 整段叙述连「先这么说」一起进片段，红。
    """
    text = ("凤姐道：“先这么说。”\n"
            + "贾母听了，也不言语。" * 10
            + "凤姐笑道：“你老慢慢说。”\n"
            + "刘姥姥道：“姑娘说得是。”\n")

    c = extract_candidates(text, ["刘姥姥"])[0]

    assert c.context.endswith("刘姥姥道：“姑娘说得是。”"), "到本句收尾引号止"
    assert "你老慢慢说" in c.context, "上一句（凤姐那句）在片段里"
    assert c.context == text[text.index("凤姐笑道：") - 60:text.rindex("”") + 1]
    assert "先这么说" not in c.context, "再上一句整个落在 60 字之外，不该进来"


# ── 引号样式：一份文本只用出现最多的那一对（补充 1-第 1 步）─────────────────

BRACKET = ("凤姐道：「你老先坐。」\n"
           "刘姥姥笑道：「我念了句『阿弥陀佛』，姑娘别见怪。」\n"
           "凤姐道：「你老慢慢说。」\n")


def test_the_book_quote_style_is_picked_by_the_most_opening_quotes():
    """`「」` 的书用 `「」` 抽；只认 `“”` 的话这类文本一条候选也抽不出来。

    变异：`_QUOTE` 改回只认 `“”` → 两条断言都红（抽出空列表）。
    """
    cands = extract_candidates(BRACKET, ["刘姥姥"])

    assert [c.line for c in cands] == ["我念了句『阿弥陀佛』，姑娘别见怪。"]
    assert [c.lead for c in cands] == ["刘姥姥笑道："]


def test_an_inner_quote_pair_is_not_a_speaking_turn_of_its_own():
    """外层 `「」` 里的 `『』` 是引语中的引语，不是另一句对话。

    三对混着匹配会把 `『阿弥陀佛』` 插进引语序列，于是凤姐那句的「上一句」变成
    「阿弥陀佛」—— 成组时给出的对方台词整条错位。

    变异：三对各自 findall 再按位置并起来 → `prev_line` 断言红。
    """
    cands = extract_candidates(BRACKET, ["凤姐"])

    assert len(cands) == 1
    assert cands[0].prev_line == "我念了句『阿弥陀佛』，姑娘别见怪。"


def test_a_tie_goes_to_the_first_pair_in_the_list():
    """开引号数并列时按 `_QUOTE_PAIRS` 的顺序取（`“”` 优先）—— 两种样式混用的文本结果
    是确定的，不随扫描顺序漂。

    变异：比较写成 `>=`（后来者居上）→ 抽到 `「」` 那两句，断言红。
    """
    text = ("贾母道：“你且听。”\n"
            "刘姥姥道：“好。”\n"
            "凤姐道：「也罢。」\n"
            "刘姥姥道：「丁。」\n")

    assert [c.line for c in extract_candidates(text, ["刘姥姥", "凤姐"])] == ["好。"]


def test_ascii_quotes_yield_no_candidates():
    """ASCII 直引号不分左右、无法按出现顺序配对（约束 12），**本轮不支持**：不猜，抽空。"""
    text = '刘姥姥笑道:"姑娘别见怪。"\n凤姐道:"你老慢慢说。"\n'

    assert extract_candidates(text, ["刘姥姥", "凤姐"]) == []


def test_a_two_name_lead_is_still_collected_so_the_model_can_reject_it():
    """引导语含第二个人名只算「多收」，不算漏 —— 归属对不对由模型读完整片段判。

    变异：改成「只认引导语里唯一的人名」→ 第 1 条被丢掉（凤姐的台词从此不进候选，
    模型再想挑也挑不到）。
    """
    cands = extract_candidates(PARA, ["刘姥姥"])
    assert "本句：凤姐忙和刘姥姥摆手道：“你老快别这样说" in render_candidates(cands)


def test_a_paren_action_in_the_lead_neither_hides_nor_invents_a_candidate():
    """引导语里的（）动作不影响归属：凤姐（笑）道 → 是凤姐的候选。

    变异：拿去掉（）后的引导语去比人名（（笑）被当成人名的一部分）→ 凤姐的两条全丢。
    """
    assert [c.line for c in extract_candidates(PARA, ["凤姐"])] == [
        "你老快别这样说，我不过是替你张罗张罗，你倒来挑我的不是。",
        "你老慢慢说，别急。",
    ]


def test_no_names_or_no_quotes_yields_nothing():
    assert extract_candidates(PARA, []) == []
    assert extract_candidates(PARA, [""]) == []
    assert extract_candidates("只有叙述，没有对话。", ["刘姥姥"]) == []
    assert extract_candidates(PARA, ["薛宝钗"]) == []


def test_rendered_numbers_are_the_same_numbers_the_caller_indexes_by():
    """渲染块里的编号就是 `Candidate.n`（从 1 起）—— 提示词与「按编号复制」共用一份清单。

    变异：渲染时改用 enumerate 的下标（0 起）→ 模型回 1 会被复制成第 2 条。
    """
    rendered = render_candidates(extract_candidates(PARA, ["刘姥姥"]))
    assert rendered.count("[1] 片段：") == 1
    assert rendered.count("[3] 片段：") == 1
    assert "[4]" not in rendered and "[0]" not in rendered


ELIDED = "凤姐笑道：“我不过是替你张罗张罗，你倒来挑我的不是，真真让人寒心。”"


def test_an_excerpt_is_checked_segment_by_segment_across_the_ellipsis():
    """「甲……乙」的节选按省略号拆开逐段核对，整串查不到也算命中。

    变异：不按省略号分段、整串去查 → 省掉的话还夹在原文里，整串必然查不到，每一处
    节选都被判成编造（本条两条断言同时变红）。
    """
    assert verbatim_in(ELIDED, "我不过是替你张罗张罗……真真让人寒心") is True
    assert verbatim_in(ELIDED, "我不过是替你张罗张罗……真真叫人寒心") is False


def test_punctuation_and_whitespace_writing_differences_do_not_matter():
    """归一化只留实义字符：全角引号、逗号有无、换行与空格都不影响命中。

    变异：用原串直接 `in`（不归一化）→ 第一、三条查不到（引号与换行都留在了串里）；
    第二条是形态断言（口癖短到三个字也照样逐字命中），不受该变异影响。
    """
    assert verbatim_in(ELIDED, "「我不过是替你张罗张罗, 你倒来挑我的不是」") is True
    assert verbatim_in("众人笑道：“无事忙来了。”", "无事忙") is True
    assert verbatim_in(ELIDED, "「我不过是替你张罗张罗\n你倒来挑我的不是」") is True


def test_one_changed_char_is_not_verbatim_and_empty_quotes_find_nothing():
    assert verbatim_in(ELIDED, "我不过是替你张罗张罗，你倒来挑我的不是，真真让人伤心") is False
    assert verbatim_in(ELIDED, "") is False
    assert verbatim_in(ELIDED, "……") is False


# ── 挑选结果的校验与拼装（WP17 补充 1：模型回编号 + 上一句说话人，文字由代码复制）──

def test_zero_out_of_range_duplicate_and_undecided_slots_are_dropped():
    """编号 0（该格不挑）/ 越界 / 重复 / 非整数 / 说话人不合格 / 缺字段 —— 一律丢，顺序即槽位顺序。

    合格 = 说话人被 `SpeakerOptions.accepts` 接受，即名单里的其他人。本人、名单外的人、
    「无法判断」、不在选项里的名字都不合格 —— 这些是模型如实作答的出口，不是可成的组。
    槽位是 `PICK_SLOTS` 个（比 `MAX_EXAMPLES` 多一倍），凑满 `MAX_EXAMPLES` 组即停。
    变异：`by_n[n]` 直接索引 → 越界抛 KeyError（调用方分不清是挑选失败还是代码错误）、
    不丢 0 → 拿 0 号索引取到错的那句、接受本人/名单外的人/「无法判断」→ 拼出没有对方名的组
    或自己接自己 —— 各红一条。
    """
    options = SpeakerOptions(others=("凤姐", "薛宝钗"), subject="刘姥姥")

    assert valid_picks(
        {"pick1": 2, "speaker1": "凤姐", "pick2": 1, "speaker2": "薛宝钗",
         "pick3": 3, "speaker3": "凤姐"}, 5, options,
    ) == [(2, "凤姐"), (1, "薛宝钗"), (3, "凤姐")]
    assert valid_picks(
        {"pick1": 0, "speaker1": "凤姐",        # 0 = 这格不挑
         "pick2": 8, "speaker2": "凤姐",        # 越界
         "pick3": 1, "speaker3": "凤姐"}, 5, options,
    ) == [(1, "凤姐")]
    assert valid_picks(
        {"pick1": 1, "speaker1": "凤姐",
         "pick2": 1, "speaker2": "薛宝钗",      # 重复编号，只留第一格
         "pick3": "3", "speaker3": "凤姐"}, 5, options,   # 非整数
    ) == [(1, "凤姐")]
    assert valid_picks(
        {"pick1": 1, "speaker1": "凤姐",
         "pick2": 2, "speaker2": UNDECIDED,     # 判不准
         "pick3": 3, "speaker3": "袭人"}, 5, options,     # 不在选项
    ) == [(1, "凤姐")]
    assert valid_picks(
        {"pick1": 1, "speaker1": "刘姥姥",      # 本人：在 enum 里，但不合格
         "pick2": 2, "speaker2": OUTSIDER}, 5, options,   # 名单外的人：同上
    ) == []
    assert valid_picks({"pick1": 4}, 5, options) == [], "缺说话人，成不了组"
    assert valid_picks(None, 5, options) == []
    assert valid_picks(2, 5, options) == [], "不是对象（非 strict 供应商可能编造）"
    assert valid_picks([{"n": 1, "prev_speaker": "凤姐"}], 5, options) == [], \
        "旧的变长数组形状不再接受（长度 strict 拦不住，改成了固定槽位）"
    assert MAX_EXAMPLES == 3
    assert PICK_SLOTS == 6
    assert UNDECIDED == "无法判断"


def test_an_example_pairs_the_previous_line_with_the_subject_line_verbatim():
    """一组示例 = 「对方名：上一句\\n角色名：本句」，两行都逐字来自原文。

    变异：少写上一句（只留本句）→ 不再成对，模型看不到对方怎么接的话；对方名写成固定
    字面量 → 第 1 条断言的 `凤姐：` 变红。
    """
    cands = extract_candidates(PARA, ["刘姥姥"])

    assert build_example(cands[1], "刘姥姥", "凤姐") == (
        "凤姐：你老快别这样说，我不过是替你张罗张罗，你倒来挑我的不是。\n"
        "刘姥姥：姑娘说的我心里熨帖，我哪敢挑姑娘的不是。"
    )
    # 上一句是裸引号（模型给不出说话人时会在校验那层被丢掉）——这里只验拼装本身。
    assert build_example(cands[2], "刘姥姥", "凤姐") == (
        "凤姐：这话怎么讲？\n"
        "刘姥姥：阿弥陀佛！我这一辈子也没见过这样的排场。"
    )


# ── 引文抽取：卡片里成对的引文（含英文双引号）与 4 字下限（卡片引文逐字核对）──

def test_an_english_double_quoted_citation_is_extracted_with_its_span():
    """卡片里的引文常用英文双引号 `"…"`：抽出来的是整段（含引号）的起止与引号内文字。

    变异：`CITATION_PAIRS` 去掉 `('"', '"')` → 空列表，两条断言同时红。
    """
    text = '他道："姑娘说得是。"\n'
    spans = quoted_spans(text)

    assert [s[2] for s in spans] == ["姑娘说得是。"]
    start, end, _ = spans[0]
    assert text[start:end] == '"姑娘说得是。"', "起止含引号本身"


def test_the_cjk_quote_styles_are_all_extracted_in_text_order():
    """`“”` 与 `「」` 都要抽，按在文里的先后返回。"""
    text = '凤姐道：“你老先坐。”\n刘姥姥笑道：「我念了句，姑娘别见怪。」\n'
    assert [s[2] for s in quoted_spans(text)] == ["你老先坐。", "我念了句，姑娘别见怪。"]


def test_an_english_quote_nested_inside_a_cjk_quote_is_extracted_too():
    """中文引号里嵌英文双引号时，外层与内层各是一条（`"…"` 不被外层吞掉）。

    变异：配对表只留 CJK → 内层那条查不到，断言红。
    """
    text = '凤姐道：“他说"我不知道"就走了。”\n'
    assert [s[2] for s in quoted_spans(text)] == ['他说"我不知道"就走了。', "我不知道"]


def test_quotes_shorter_than_the_minimum_after_normalizing_are_skipped():
    """归一化后不足 `CITATION_MIN_CHARS` 字的引文太短、子串命中没有分辨力：不返回。

    变异：去掉下限 → `"好。"` 也被抽出来，第一条断言红。
    """
    text = '他道："好。"\n'
    assert quoted_spans(text) == []
    assert CITATION_MIN_CHARS == 4
    assert len(normalize("好")) < CITATION_MIN_CHARS


# ── 异体字并字：把「著」「着」当同一个字（docs/specs/quote-variant-fold.md）──────
#
# 并字表只放有依据的异体字组（《異體字字典》A03506），写在 `normalize` 内 —— 抽取与
# 核对、产品与验收共用同一个出口。表里没有的字组（如 唬/吓）不许并。

def test_a_variant_character_counts_as_the_same_character():
    """原文写「著」、引用写「着」—— 逐字核对视为同一个字。

    真实案例：宝玉卡 `values[3]`「活著……化灰化烟」的引号被误撤回（卡上「着」、本地
    原文「著」）。变异：删掉 `_VARIANT_FOLD` → 本条断言红。
    """
    assert verbatim_in("你放心，活著咱们一处", "活着咱们一处") is True


def test_a_non_variant_character_is_not_folded():
    """并字表只放有依据的异体字组：「唬」不在表里（本地原文那一处写的是「吓」），措辞
    不逐字的引文仍判假 —— 否则并字从「并用字差异」滑成「并近义字」。

    变异：把「唬→吓」也加进并字表 → 本条断言红。
    """
    assert verbatim_in("吓的不敢作声", "唬的不敢作声") is False


# ── 繁转简：原文是繁体、引文是简体也算逐字命中（docs/specs/quote-script-fold.md）──
#
# 原文（公版《红楼梦》繁体本）与引文（模型转述时用的简体）字形不同、字义相同。归一化先
# 经 OpenCC `t2s` 把两侧落回同一字形集；不做的话繁体原文里的真引文一律被判「查不到」，
# 端到端真跑因此撤回 6 条（留下的口癖是繁体，被撤回的全是模型转述的简体）。

TRAD_SOURCE = (
    "寶玉便走近黛玉身邊坐下，又細細打量一番，因問：「妹妹可曾讀書？」"
    "黛玉道：「不曾讀，只上了一年學，些須認得幾個字。」\n"
)


def test_a_simplified_quote_matches_a_traditional_source():
    """原文繁体、引文简体 —— 逐字核对视为命中。

    变异：去掉 `t2s` → 本条断言红（两侧字形不同，子串查不到）。
    """
    assert verbatim_in(TRAD_SOURCE, "不曾读，只上了一年学，些须认得几个字") is True


def test_a_mixed_script_quote_matches_a_traditional_source():
    """繁简夹杂的引文同样命中 —— 模型转述常见一半照抄、一半转简。

    变异：去掉 `t2s` → 本条断言红。
    """
    assert verbatim_in(TRAD_SOURCE, "不曾讀，只上了一年学，些须認得几个字") is True


def test_the_converter_is_built_once_not_per_call(monkeypatch):
    """转换器是模块级单例：`normalize` 不每次新建（整本 90 万字、每卡几十条引文都重建
    转换器是纯浪费；单例只构造一次，常驻约 +8MB）。

    变异：在 `normalize` 里每次新建 `OpenCC("t2s")` → 计数断言红。探针接在
    `opencc.OpenCC` 上，并先断言 `core.quotes` 用的就是这个模块 —— 若改成
    `from opencc import OpenCC`，探针落空会假绿，所以那条断言也守着绑定。
    """
    import opencc
    import core.quotes as q

    built: list[tuple] = []
    real = opencc.OpenCC

    def counting(*a, **k):
        built.append(a)
        return real(*a, **k)

    monkeypatch.setattr(opencc, "OpenCC", counting)
    assert q.opencc is opencc, "探针没接在被测的绑定上（quotes 应 `import opencc` 模块）"

    for _ in range(50):
        q.normalize("不曾讀，只上了一年學")

    assert built == [], "normalize 每次调用都新建了转换器（应为模块级单例）"
