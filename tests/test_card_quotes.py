# -*- coding: utf-8 -*-
"""`core.card_quotes.retract_unverified`：卡片里查不到的引文去引号、口癖对不上的整条删。

**为什么单开一层。** 引文（角色自述、他人评价）是卡片里最容易被编造的成分：读着像原文，
却查无此句。WP17 已让「对话示例」由代码从原文复制、逐字由构造保证；这一层把同样的保证
扩到卡片其余字段里的引文与口癖。判据与验收共用 `core.quotes`，产品保证的与验收证明的
不分家。

**夹具是公版《红楼梦》原句**（公有领域），原文里放真引文，卡片里混入编造的引文/口癖，
逐条断言：编造的被改、真的不动、清单外的字段一律不碰。

**变异。** ① 改为原样保留编造引文 → 去引号断言红；② 改为整条删除 → 文字保留断言红；
③ 口癖改为去引号或不处理 → 口癖断言红；④ 检查所有字段 → taboo_words/first_message/note
被误改，断言红；⑤ 逐条 `verbatim_in` 而非先归一化一次 → T3 整本归一化计数变红。
"""
from __future__ import annotations

from core.card_quotes import retract_unverified
from core.schema import CharacterCard

# 公版《红楼梦》原句（公有领域）。真引文逐字在此，编造的引文一定不在此。
SOURCE = (
    "刘姥姥道：“我掂着这杯体重，断乎不是杨木，这一定是黄松的。”\n"
    "凤姐笑道：“你老慢慢说。”\n"
    "刘姥姥叹道：“阿弥陀佛！我这一辈子也没见过这样的排场。”\n"
)

FABRICATED = "你就说醉话，我是要进城瞧瞧去的"      # 原文里没有（前半句是拼接的）
REAL = "我掂着这杯体重，断乎不是杨木，这一定是黄松的"

CARD = {
    "name": "刘姥姥",
    "key_memories": [
        f'凤姐叫她"{FABRICATED}"，她便连夜进城。',       # 编造 → 去引号、留文字
        f'她能掂出杯子："我掂着这杯体重，断乎不是杨木，这一定是黄松的"。',  # 真引文 → 不动
    ],
    "personality_traits": ['爱念佛，常说"阿弥陀佛！我这一辈子也没见过这样的排场"'],
    "speaking_style": {
        "catchphrases": ["你老慢慢说", "无事忙"],   # 后者原文里没有 → 整条删
        "taboo_words": ['"卑职"'],                  # 清单外 → 不动（引号里本来就不是原文）
    },
    "first_message": '爷们好，我说"这句是编的"给你听。',   # 清单外 → 不动
    "relationships": [
        {"target": "凤姐", "relation": "远房亲戚",
         "attitude": f'又敬又怕，常说"{FABRICATED}"',      # 清单内 → 去引号、关系保留
         "note": '她心善，"这句 note 也是编的"'},           # 清单外 → 不动
    ],
}


def _retract(d: dict = CARD):
    card = CharacterCard.model_validate(d)
    return card, retract_unverified(card, SOURCE)


def test_a_fabricated_quote_loses_its_quotes_but_keeps_its_words():
    """查不到的引文去掉引号、保留文字，并在撤回清单里报出所在字段与原引文。

    变异：改为原样保留 → 第一条断言红；改为整条删除 → 关键记忆（事件本身正确）整个没了。
    """
    card, (new, retracted) = _retract()

    assert new.key_memories[0] == f"凤姐叫她{FABRICATED}，她便连夜进城。"
    assert card.key_memories[0] == f'凤姐叫她"{FABRICATED}"，她便连夜进城。', "原卡不就地改"
    assert {"field": "key_memories[0]", "quote": FABRICATED} in retracted


def test_a_real_quote_and_a_real_catchphrase_are_left_alone():
    """逐字出自原文的引文与口癖原样保留（去引号那条只对查不到的生效）。"""
    _, (new, retracted) = _retract()

    assert new.key_memories[1] == CARD["key_memories"][1]
    assert new.personality_traits == CARD["personality_traits"]
    assert new.speaking_style.catchphrases == ["你老慢慢说"]
    assert not any(r["quote"] == "你老慢慢说" for r in retracted), "真口癖不该被撤回"


def test_a_catchphrase_that_is_not_in_the_source_is_deleted_whole():
    """口癖声明「是原话」，对不上就整条删（没有可保留的部分）—— 不设字数下限。

    变异：口癖改为去引号或不处理 → 第一条断言红。
    """
    _, (new, retracted) = _retract()

    assert new.speaking_style.catchphrases == ["你老慢慢说"]
    assert {"field": "speaking_style.catchphrases", "quote": "无事忙"} in retracted


def test_fields_outside_the_verified_list_are_never_touched():
    """清单外的字段（忌讳词 / 开场白 / note）一个字符都不动，即便引号里是编造的。

    变异：改为检查所有字段 → 三条断言同时红。
    """
    _, (new, _) = _retract()

    assert new.speaking_style.taboo_words == CARD["speaking_style"]["taboo_words"]
    assert new.first_message == CARD["first_message"]
    assert new.relationships[0].note == CARD["relationships"][0]["note"]


def test_an_attitude_quote_is_stripped_and_the_relationship_survives():
    """`relationships[].attitude` 在清单内：编造的引文去引号，关系条目本身（人名/关系）保留。

    变异：清单漏掉 attitude → 引号还在，第一条断言红。
    """
    _, (new, retracted) = _retract()

    assert new.relationships[0].attitude == f"又敬又怕，常说{FABRICATED}"
    assert (new.relationships[0].target, new.relationships[0].relation) == ("凤姐", "远房亲戚")
    assert {"field": "relationships[0].attitude", "quote": FABRICATED} in retracted


def test_the_source_is_normalized_exactly_once(monkeypatch):
    """整本原文只归一化一次：45 条引文拿同一份归一化结果比，不是每条再归一化一遍原文。

    变异：`retract_unverified` 改回逐条 `verbatim_in(content, q)` → 整本原文被归一化
    45 次，计数断言红。探针同时接在 `core.card_quotes` 与 `core.quotes` 两处 normalize
    绑定上，并断言「确实跑到」——只接一处会被绕开入口的变异骗成假绿。
    """
    import core.card_quotes as cq
    import core.quotes as q

    seen: list[str] = []
    real = q.normalize

    def counting(s):
        seen.append(s)
        return real(s)

    monkeypatch.setattr(cq, "normalize", counting)
    monkeypatch.setattr(q, "normalize", counting)

    card = CharacterCard.model_validate({
        "name": "刘姥姥",
        "personality_traits": [f'{i}：“{REAL}”' for i in range(45)],
    })
    _, retracted = retract_unverified(card, SOURCE)

    assert retracted == [], "45 条都是真引文，不该有撤回"
    assert seen, "计数探针没接上任何一处 normalize 绑定"
    assert sum(1 for s in seen if s == SOURCE) == 1


# ── 异体字：原文「著」、卡上「着」（docs/specs/quote-variant-fold.md）──────────
#
# 并字写在 `core.quotes.normalize` 内，产品这一侧取的就是它 —— 不是异体字的差异不该算
# 差异（真实案例：宝玉卡 `values[3]` 的引号曾被误撤回；口癖则是整条被删）。

VARIANT_SOURCE = "宝玉道：“活著，咱们一处活著，不活著，咱们一处化灰化烟。”\n"
VARIANT_QUOTE = "活着，咱们一处活着，不活着，咱们一处化灰化烟"


def test_a_quote_differing_only_by_a_variant_character_keeps_its_quotes():
    """卡上引文只有「著/着」之差：引号保留，撤回清单为空。

    变异：删掉并字表 → 引号被去掉，两条断言红。
    """
    card = CharacterCard.model_validate({
        "name": "宝玉",
        "values": [f"生死相托的痴情：对紫鹃说“{VARIANT_QUOTE}”（第五十七回）。"],
    })
    new, retracted = retract_unverified(card, VARIANT_SOURCE)

    assert retracted == []
    assert new.values == card.values, "引号保留"


def test_a_catchphrase_differing_only_by_a_variant_character_is_kept():
    """口癖同理：卡上「着」、原文「著」不该整条删。

    变异：删掉并字表 → 口癖被删、撤回清单非空，两条断言红。
    """
    card = CharacterCard.model_validate({
        "name": "宝玉",
        "speaking_style": {"catchphrases": [VARIANT_QUOTE]},
    })
    new, retracted = retract_unverified(card, VARIANT_SOURCE)

    assert retracted == []
    assert new.speaking_style.catchphrases == [VARIANT_QUOTE]
