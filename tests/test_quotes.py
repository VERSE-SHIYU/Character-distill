# -*- coding: utf-8 -*-
"""`core/quotes.py` 的判据锁：谁被抽成候选、一段文字算不算逐字出自原文。

**为什么是纯函数用例。** 抽取与核对不碰 HTTP、不碰库也不碰模型，却是 WP17 的地基：
产品按编号从原文复制对话示例、验收按同一份判定核对成品卡，两边共用本模块。地基判错
只会在真跑时以「对话示例 0/3 命中」的形态出现，届时既贵又看不出是抽错了还是模型挑错了。

**变异。** 每条用例都配一个「少做一步就红」的反例：不按省略号分段、引导语取整段叙述
而不是最后一个句读、渲染编号改成 0 起。
"""
from __future__ import annotations

from core.quotes import (
    MAX_EXAMPLES,
    build_example,
    extract_candidates,
    render_candidates,
    speaker_in,
    valid_picks,
    verbatim_in,
)

# 真实形态：整段的叙述 + 引导语 + 引号。《红楼梦》的对话一律「“……”」，说话人写在紧贴
# 引号的引导语里；这里刻意混入四种形态 —— 引导语含两个人名、引导语带（）动作、
# 无引导语的裸引号、以及「最后一个句读之后才是引导语」的多句叙述。
PARA = (
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
    assert (cands[0].prev_lead, cands[0].prev_line) == ("", ""), "首句没有上一句"

    assert cands[1].lead == "刘姥姥笑道："
    assert cands[1].prev_lead == "凤姐忙和刘姥姥摆手道："
    assert cands[1].prev_line.startswith("你老快别这样说")

    # 裸引号「这话怎么讲？」不进候选（无引导语 → 归不到人），但它是下一句的上一句。
    assert cands[2].lead == "刘姥姥叹道："
    assert (cands[2].prev_lead, cands[2].prev_line) == ("", "这话怎么讲？")


def test_a_two_name_lead_is_still_collected_so_the_model_can_reject_it():
    """引导语含第二个人名只算「多收」，不算漏 —— 归属对不对由模型读完整引导语判。

    变异：改成「只认引导语里唯一的人名」→ 第 1 条被丢掉（凤姐的台词从此不进候选，
    模型再想挑也挑不到）；渲染时不给引导语 → 模型判不出这句其实是凤姐说的。
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


# ── 引号样式：一份文本只用出现最多的那一对（补充 1-第 1 步）─────────────────

BRACKET = ("刘姥姥笑道：「我念了句『阿弥陀佛』，姑娘别见怪。」\n"
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

    变异：比较写成 `>=`（后来者居上）→ 抽到 `「」` 那句，断言红。
    """
    text = "刘姥姥道：“好。”\n凤姐道：「也好。」\n"

    assert [c.line for c in extract_candidates(text, ["刘姥姥", "凤姐"])] == ["好。"]


def test_ascii_quotes_yield_no_candidates():
    """ASCII 直引号不分左右、无法按出现顺序配对（约束 12），**本轮不支持**：不猜，抽空。"""
    text = '刘姥姥笑道:"姑娘别见怪。"\n凤姐道:"你老慢慢说。"\n'

    assert extract_candidates(text, ["刘姥姥", "凤姐"]) == []


def test_rendered_numbers_are_the_same_numbers_the_caller_indexes_by():
    """渲染块里的编号就是 `Candidate.n`（从 1 起）—— 提示词与「按编号复制」共用一份清单。

    变异：渲染时改用 enumerate 的下标（0 起）→ 模型回 1 会被复制成第 2 条。
    """
    rendered = render_candidates(extract_candidates(PARA, ["刘姥姥"]))
    assert rendered.count("[1] 上一句：") == 1
    assert rendered.count("[3] 上一句：") == 1
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


# ── 挑选结果的校验与拼装（WP17：模型只回编号，文字由代码从原文复制）──────────

def test_out_of_range_and_duplicate_picks_are_dropped_and_at_most_three_survive():
    """编号越界 / 重复 / 非整数一律丢，顺序保留，最多 3 个。

    变异：`[by_n[p] for p in picks]` 直接索引 → 越界抛 KeyError（调用方分不清是挑选
    失败还是代码错误）、重复拼出两组一样的话、多于 3 组照单全收 —— 三条断言各红一条。
    """
    assert valid_picks([2, 2, 1, 0, 8, 3, 4], 5) == [2, 1, 3]
    assert valid_picks([1, "2", True, 2.0, None, 3], 5) == [1, 3]
    assert valid_picks(None, 5) == []
    assert valid_picks(2, 5) == [], "不是数组（非 strict 供应商可能编造）"
    assert MAX_EXAMPLES == 3


def test_the_other_speaker_comes_from_the_previous_lead_only_when_unambiguous():
    """上一句的引导语里恰好一个候选名 → 用它；零个、或多个 → 判不出。

    只收「恰好一个」：`凤姐忙和刘姥姥摆手道：`（约束 7 的 15%）里两个名字都在，挑第一
    个会把这句记成凤姐说的 —— 宁可退化成「对方」也不给一个可能错的归属。
    """
    assert speaker_in("刘姥姥笑道：", ["刘姥姥", "凤姐"]) == "刘姥姥"
    assert speaker_in("凤姐忙和刘姥姥摆手道：", ["刘姥姥", "凤姐"]) == ""
    assert speaker_in("众人道：", ["刘姥姥", "凤姐"]) == ""
    assert speaker_in("刘姥姥笑道：", ["刘姥姥", "刘姥姥"]) == "刘姥姥", "别名重复仍是一个人"
    assert speaker_in("刘姥姥笑道：", []) == ""


def test_an_example_pairs_the_previous_line_with_the_subject_line_verbatim():
    """一组示例 = 「对方名：上一句\\n角色名：本句」，两行都逐字来自原文。

    变异：少写上一句（只留本句）→ 不再成对，模型看不到对方怎么接的话；把「对方名」写成
    固定字面量 → 第 1 条断言的 `凤姐：` 变红。
    """
    cands = extract_candidates(PARA, ["刘姥姥"])

    assert build_example(cands[1], "刘姥姥", ["凤姐"]) == (
        "凤姐：你老快别这样说，我不过是替你张罗张罗，你倒来挑我的不是。\n"
        "刘姥姥：姑娘说的我心里熨帖，我哪敢挑姑娘的不是。"
    )
    # 上一句没有引导语（裸引号）→ 归属判不出，写「对方」而不是编一个人名。
    assert build_example(cands[2], "刘姥姥", ["凤姐"]) == (
        "对方：这话怎么讲？\n"
        "刘姥姥：阿弥陀佛！我这一辈子也没见过这样的排场。"
    )
