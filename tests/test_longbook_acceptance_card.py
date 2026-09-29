# -*- coding: utf-8 -*-
"""`tests/perf/longbook_acceptance.py` 的 §10 C4 卡片核对锁：对话 / 口癖 / 引文 / 关系重复。

**为什么入库。** 旧 `hits()` 把整条 `dialogue_examples`（多行、含「说话人：」标签与
（…）动作说明）当**整体**子串去原文查 —— 原文里只有台词本身，于是这个判据对任何一张
卡都恒报 0/3，而它只报不停：三个月后没人看得出它从来没命中过。引文同理（藏在别的字段
里，一句都没查过），关系重复则是从来没人查过。

**纯函数而不是真卡片。** `dialogue_hits` / `catchphrase_misses` / `quote_misses` /
`duplicate_targets` 不碰 HTTP、不碰库，所以每条判据都配一条「像但它不是」的反例 ——
少拆一层标签、改一个字、少算一次重复，都会红。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "perf"))

import longbook_acceptance as acc  # noqa: E402

SOURCE = (
    "凤姐笑道：「我不过是替你张罗张罗，你倒来挑我的不是。」\n"
    "宝玉道：「好姐姐，你别说这些，我听着心里难受。」\n"
    "贾母道：「人人都说那宝丫头好，会做人，很大方，今日看来果然不错。」\n"
)


def test_a_labelled_multiline_group_hits_and_a_one_char_change_misses():
    """多行「说话人：台词（动作）」拆句后逐句命中；缺一个字就整组算 miss。

    挡住两条变异：① 不拆行、整组归一后查原文（标签与换行留在串里）→ hit 0；
    ② 拆行但不去（…）动作说明（`笑` 留在台词里）→ 第二句查不到 → hit 0。
    """
    group = ("凤姐：我不过是替你张罗张罗，你倒来挑我的不是。\n"
             "宝玉：（笑）好姐姐，你别说这些，我听着心里难受。")
    assert acc.dialogue_hits([group], SOURCE) == {"n": 1, "hit": 1, "miss": []}

    broken = acc.dialogue_hits([group.replace("好姐姐", "好妹妹")], SOURCE)
    assert broken["hit"] == 0 and len(broken["miss"]) == 1


def test_a_dialogue_excerpt_spliced_with_ellipsis_still_hits_the_source():
    """「甲……乙」这种节选按省略号拆段逐段核对 —— 验收与产品共用 `core/quotes` 的判定。

    挡住变异：验收侧留一份自己的「归一后整串查原文」（旧 `_norm`）—— 归一丢掉的省略号
    把两段拼成一句，原文里这两段并不相连，于是产品的合法节选被验收判成编造，报错的
    是验收不是产品。
    """
    src = ("宝玉道：「好姐姐，你别说这些，我听着心里难受。你别怕。」\n"
           "黛玉道：「我知道了。」\n")
    group = "宝玉：好姐姐，你别说这些……你别怕。"
    assert acc.dialogue_hits([group], src) == {"n": 1, "hit": 1, "miss": []}


def test_a_quote_changed_by_one_char_is_reported_with_its_field():
    """卡片里任意字符串字段的成对引文都要查原文；查不到的原样带回字段名。

    挡住两条变异：① 只查 `dialogue_examples` / `catchphrases`（旧写法）—— `key_memories`
    里的引文一句都没查过 → 反例仍判为 0 条；② 不设 4 字下限，把太短的引文（子串命中
    没有分辨力）也算进去 → 正例报出 2 条。
    """
    ok = {"key_memories": ["贾母道：「人人都说那宝丫头好，会做人，很大方」"]}
    assert acc.quote_misses(ok, SOURCE) == []

    changed = {"key_memories": ["贾母道：「人人都说那薛丫头好，会做人，很大方」"]}
    assert acc.quote_misses(changed, SOURCE) == [
        {"field": "key_memories[0]", "quote": "人人都说那薛丫头好，会做人，很大方"}]

    short = {"speaking_style": {"tone": "「冷淡」"}}   # 归一后 2 字 < 4，不查
    assert acc.quote_misses(short, SOURCE) == []


def test_a_quote_in_english_double_quotes_is_checked_too():
    """卡片里的引文大量用英文双引号 `"`：验收漏掉这一对，「引文查不到 0」就是假的 0。

    挡住变异：配对表改回不含 `"`（旧 `_QUOTE_PAIRS`）→ 反例仍判 0 条。
    """
    ok = {"key_memories": ['贾母道："人人都说那宝丫头好，会做人，很大方"']}
    assert acc.quote_misses(ok, SOURCE) == []

    bad = {"key_memories": ['贾母道："人人都说那薛丫头好，会做人，很大方"']}
    assert acc.quote_misses(bad, SOURCE) == [
        {"field": "key_memories[0]", "quote": "人人都说那薛丫头好，会做人，很大方"}]


def test_only_the_verified_fields_are_checked():
    """只查产品也核的那份清单：`first_message` / `taboo_words` 的引号里本就不是原文。

    标签形状也要与产品一致（`relationships[0].attitude`）—— 两边各写一份字段清单，
    「核了哪些字段」迟早分家，验收就再也证明不了产品。

    挡住变异：改回扫所有字符串字段（旧 `_strings`）→ 清单外那两条被报出来，断言红。
    """
    card = {
        "first_message": '娘，我说"这句是编的"给你听。',
        "speaking_style": {"taboo_words": ['"卑职不敢"']},
        "relationships": [{"target": "贾母", "relation": "祖孙",
                           "attitude": '常念"人人都说那薛丫头好，会做人，很大方"'}],
    }
    assert acc.quote_misses(card, SOURCE) == [
        {"field": "relationships[0].attitude",
         "quote": "人人都说那薛丫头好，会做人，很大方"}]


def test_a_catchphrase_changed_by_one_char_is_reported():
    """口癖是本人原话里的固定说法，须逐字出现在原文；**不设字数下限**。

    挡住两条变异：① 不查口癖（读数一栏恒为 0 条）→ 反例仍判 0；② 套用引文那条 4 字
    下限 → 「无事忙」这种 3 字口癖被跳过，反例判 0。
    """
    src = "众人见他来了，都笑道：「无事忙来了。」宝玉道：「好姐姐，你别恼。」"
    assert acc.catchphrase_misses(["无事忙", "好姐姐"], src) == []
    assert acc.catchphrase_misses(["无忙事", "好姐姐"], src) == ["无忙事"]


def test_a_repeated_relationship_target_is_reported_with_its_count():
    """重复的 target 连同次数一起报出，次数多的在前。

    挡住变异：把 `target` 读成别的字段名（如 `relation`）—— 反例里 `relation` 各不相同，
    于是重复数报成 0。
    """
    rels = [{"target": "贾母", "relation": "祖孙"},
            {"target": "王夫人", "relation": "母子"},
            {"target": "贾母", "relation": "祖孙"},
            {"target": "贾母", "relation": "长辈"},
            {"target": "王夫人", "relation": "母子"}]
    assert acc.duplicate_targets(rels) == [["贾母", 3], ["王夫人", 2]]
    assert acc.duplicate_targets([{"target": "贾母"}]) == []


def _clean_env() -> dict:
    """A 的对齐门逐项相等 —— 让 `over_limit` 只可能因卡片判据而停下。"""
    return {
        "a1": [(k, v, v) for k, v in (
            ("chunk_size", acc.EXPECTED_CHUNK_SIZE),
            ("map_concurrency", acc.EXPECTED_MAP_CONCURRENCY),
            ("model", acc.EXPECTED_MODEL),
            ("thinking", "关"),
        )],
        "chars": acc.EXPECTED_CHARS,
        "identify_chunks": acc.EXPECTED_IDENTIFY_CHUNKS,
    }


def _stats(card) -> dict:
    return {"mode": "distill", "elapsed_s": 10.0, "stages": {}, "final": "done", "card": card}


# ── 异体字：原文「著」、卡上「着」（docs/specs/quote-variant-fold.md）──────────
#
# 验收与产品共用 `core/quotes.normalize` 这一处并字：卡上「着」不该被验收报成编造。

VARIANT_SOURCE = "宝玉道：「活著，咱们一处活著，不活著，咱们一处化灰化烟。」\n"
VARIANT_QUOTE = "活着，咱们一处活着，不活着，咱们一处化灰化烟"


def test_a_quote_differing_only_by_a_variant_character_is_not_reported():
    """卡上引文只有「著/着」之差：`quote_misses` 返回空。

    变异：删掉并字表 → 该引文被判编造，返回非空，断言红。
    """
    card = {"values": [f"生死相托的痴情：对紫鹃说「{VARIANT_QUOTE}」（第五十七回）。"]}
    assert acc.quote_misses(card, VARIANT_SOURCE) == []


def test_a_catchphrase_differing_only_by_a_variant_character_is_not_reported():
    """口癖同理：卡上「着」、原文「著」不该整条报 miss。

    变异：删掉并字表 → 口癖报 miss，断言红。
    """
    assert acc.catchphrase_misses([VARIANT_QUOTE], VARIANT_SOURCE) == []


def test_the_card_checks_stop_the_run():
    """四条判据要真的挂在 `over_limit` 上，不是只从 `print_distill` 打出来。

    挡住变异：判据只进读数不进停下条件 —— 读数报「引文查不到 1」，`over_limit` 却不认，
    验收仍报「未命中停下条件」。
    """
    counts = acc.count_log(None)   # 全「未提供」：日志计数一条不命中
    assert acc.over_limit(_stats({"valid": True}), counts, _clean_env()) == []

    why = acc.over_limit(_stats({
        "valid": True,
        "dialogue": {"n": 3, "hit": 2, "miss": ["缺一句"]},
        "catchphrase_miss": ["查无此口癖"],
        "quote_miss": [{"field": "key_memories[0]", "quote": "查无此句"}],
        "dup_targets": [["贾母", 3]],
    }), counts, _clean_env())
    assert len(why) == 4 and all(
        "对话示例" in w or "口癖" in w or "引文" in w or "关系重复" in w for w in why), why
