# -*- coding: utf-8 -*-
"""对话示例「找台词、配上一句、标说话人」目标检查（docs/specs/dialogue-pairing.md §1）。

现状（main 51b214b）：
- 找台词只认「引号前、同一句里有角色名」。署名写在引号后（“可恶！”四叔说。）、引导语与
  引号之间隔了换行（闰土又对我说：⏎“……”）、正文全角而名单半角（小Ｄ / 小D）的台词都
  找不到；叙述里带引号的词（“退一步想”）反而被当成台词。
- 「上一句」取紧挨着的前一个引号，不管它是不是台词、隔了多远。
- 「上一句是谁说的」只能从名单里的其他人和「无法判断」里选；上一句其实是角色本人或名单外
  的人说的时候，选项里没有正确答案。
- 只有 3 个格子，模型如实标出不能成对的候选时，格子就被占光。

目标：角色有台词就找得到；配成的一对是真的一问一答；上一句的说话人标得对，标不出就不成组。

走真实代码：`core.quotes.extract_candidates`、`Distiller.dialogue_candidates` /
`pick_dialogue_examples`（模型用假适配器）。预言独立于被测代码：原文是本文件的字面量
（K1–K3、K7、K10 取自鲁迅公版原文，一字未改），期望是人读原文得出的。

找得到
K1 署名在引号后 → 是候选；给模型的片段带上引号后的署名
K2 引导语与引号之间隔了换行 → 是候选
K3 正文全角、名单半角 → 是候选
不多收
K4 叙述里带引号的词不是台词 → 不是候选
K5 上一句是带引号的词 → 不成对，不是候选
K6 两句之间的叙述超过两句 → 不成对；恰好两句仍成对
K7 引号后的署名直接接着下一个引号（“……”孔乙己答道，“……”）→ 不算前一句的署名（main 上就绿：回归守卫）
标得对
K8 说话人选项 = 名单里的其他人 + 本人 + 名单外的人 + 无法判断
K9 模型标「本人 / 名单外的人 / 无法判断」的格子被丢弃，卡里不出现自己接自己
K10 赵太爷：上一句是本人的那格丢弃，后面的格子补上
格子
K11 6 个格子；前 3 格都不合格时，用后 3 格凑满 3 组
K12 合格的超过 3 组时，只取前 3 组（按格子顺序）（main 上就绿：回归守卫）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.distiller import Distiller
from core.quotes import extract_candidates

# ── 鲁迅公版原文（一字未改）────────────────────────────────────────────────
# 《祝福》：卫老婆子说完一长段，四叔只回一句；署名「四叔说。」在引号后。
ZHUFU = (
    "“阿呀阿呀，我真上当。我这回，就是为此特地来说说清楚的。她来求我荐地方，我那里料得到是"
    "瞒着她的婆婆的呢。对不起，四老爷，四太太。总是我老发昏不小心，对不起主顾。幸而府上是"
    "向来宽洪大量，不肯和小人计较的。这回我一定荐一个好的来折罪……。”\n"
    "“然而……。”四叔说。"
)
# 《故乡》：「闰土又对我说：」之后换行才是引号。
GUXIANG = (
    "第二日，我便要他捕鸟。他说：\n"
    "“这不能。须大雪下了才好。我们沙地上，下了雪，我扫出一块空地来，用短棒支起一个大竹匾，"
    "撒下秕谷，看鸟雀来吃时，我远远地将缚在棒上的绳子只一拉，那鸟雀就罩在竹匾下了。什么都有："
    "稻鸡，角鸡，鹁鸪，蓝背……”\n"
    "我于是又很盼望下雪。\n"
    "闰土又对我说：\n"
    "“现在太冷，你夏天到我们这里来。我们日里到海边捡贝壳去，红的绿的都有，鬼见怕也有，"
    "观音手也有。晚上我和爹管西瓜去，你也去。”"
)
# 《阿Q正传》：正文是全角「小Ｄ」。
XIAO_D = "“畜生！”阿Q怒目而视的说，嘴角上飞出唾沫来。\n“我是虫豸，好么？……”小Ｄ说。"
# 《阿Q正传》：“退一步想”是叙述里带引号的词。
TERM = "小尼姑之流是阿Q本来视若草芥的，但世事须“退一步想”，所以他便赶紧拔起四个萝卜，拧下青叶，兜在大襟里。"
# 《孔乙己》：「孔乙己很颓唐的仰面答道，」是下一句的引导语，不是掌柜那句的署名。
KONG = (
    "见了我，又说道，“温一碗酒。”掌柜也伸出头去，一面说，“孔乙己么？你还欠十九个钱呢！”"
    "孔乙己很颓唐的仰面答道，“这……下回还清罢。这一回是现钱，酒要好。”"
)
# 《阿Q正传》：赵太爷连说两句（“完了？”……“那里会完得这样快呢？”），后一句的上一句是他本人。
ZHAO = (
    "“太爷！”阿Q似笑非笑的叫了一声，在檐下站住了。\n"
    "“阿Q，听说你在外面发财，”赵太爷踱开去，眼睛打量着他的全身，一面说。“那很好，那很好的。"
    "这个，……听说你有些旧东西，……可以都拿来看一看，……这也并不是别的，因为我倒要……”\n"
    "“我对邹七嫂说过了。都完了。”\n"
    "“完了？”赵太爷不觉失声的说，“那里会完得这样快呢？”\n"
    "“那是朋友的，本来不多。他们买了些，……”\n"
    "“总该还有一点罢。”\n"
    "“现在，只剩了一张门幕了。”\n"
    "“就拿门幕来看看罢。”赵太太慌忙说。"
)

# ── 构造文本（只为把一条规则单独拎出来）────────────────────────────────────
# 七句：凤姐与刘姥姥一问一答，刘姥姥有 7 条候选（上一句都是凤姐的话）。
SEVEN = "".join(
    f"凤姐道：“第{i}问，你老说呢？”\n刘姥姥道：“第{i}答，姑娘说得是。”\n" for i in range(1, 8))
OTHERS = [{"name": "凤姐", "aliases": ["凤丫头"]}]


def _lines(text, names):
    return [c.line for c in extract_candidates(text, names)]


# ── 找得到 ─────────────────────────────────────────────────────────────────

def test_k1_a_line_signed_after_the_quote_is_found_and_its_fragment_shows_the_signature():
    cands = extract_candidates(ZHUFU, ["鲁四老爷", "四叔", "四老爷"])

    assert [c.line for c in cands] == ["然而……。"]
    assert cands[0].prev_line.startswith("阿呀阿呀，我真上当。")
    assert cands[0].context.endswith("“然而……。”四叔说。"), "模型得看见引号后的署名才判得出是谁说的"
    assert cands[0].context in ZHUFU, "片段仍是原文的连续子串"


def test_k2_a_lead_separated_from_its_quote_by_a_newline_is_found():
    cands = extract_candidates(GUXIANG, ["闰土"])

    assert len(cands) == 1
    assert cands[0].line.startswith("现在太冷，你夏天到我们这里来。")
    assert cands[0].prev_line.startswith("这不能。须大雪下了才好。")


def test_k3_a_fullwidth_name_in_the_text_matches_the_halfwidth_name_in_the_roster():
    assert _lines(XIAO_D, ["小D", "小Don"]) == ["我是虫豸，好么？……"]


# ── 不多收 ─────────────────────────────────────────────────────────────────

def test_k4_a_quoted_term_in_narration_is_not_a_spoken_line():
    assert _lines(TERM, ["小尼姑"]) == []
    # 同一规则的最小形态：前面有一句真台词可当上一句，带引号的词也不成为候选。
    text = "凤姐道：“请坐。”\n刘姥姥看见墙上的“寿”字，不敢作声。\n"
    assert _lines(text, ["刘姥姥"]) == []


def test_k5_a_quoted_term_cannot_be_the_previous_line():
    text = "墙上挂着一个“寿”字。\n凤姐笑道：“你老快别这样说。”\n"

    assert _lines(text, ["凤姐"]) == []


def test_k6_two_lines_more_than_two_sentences_apart_are_not_a_pair():
    far = "凤姐道：“请坐。”\n众人散了。天也黑了。灯也点上了。刘姥姥道：“我们庄家人。”\n"
    near = "凤姐道：“请坐。”\n众人散了。天也黑了。刘姥姥道：“我们庄家人。”\n"

    assert _lines(far, ["刘姥姥"]) == []
    assert _lines(near, ["刘姥姥"]) == ["我们庄家人。"]


def test_k7_a_signature_that_runs_into_the_next_quote_belongs_to_the_next_quote():
    assert _lines(KONG, ["孔乙己"]) == ["这……下回还清罢。这一回是现钱，酒要好。"]


# ── 标得对 / 格子 ──────────────────────────────────────────────────────────

class _PickLLM:
    """只实现 `select_by_schema` 的假适配器：记下实参，回一个固定参数。"""

    def __init__(self, args: dict) -> None:
        self.args = args
        self.seen: dict = {}
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self._model = "fake"

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        self.seen.update(system=system_prompt, messages=messages, function=function)
        return self.args


def _pick(args, content, name, aliases, others):
    llm = _PickLLM(args)
    d = Distiller(llm=llm, config_path=None)
    candidates = d.dialogue_candidates(content, name, aliases, [{"name": name}, *others])
    return llm, d.pick_dialogue_examples(candidates, name, others)


def test_k8_the_speaker_options_include_the_subject_and_an_outsider():
    llm, _ = _pick({"pick1": 1, "speaker1": "凤姐"}, SEVEN, "刘姥姥", [], OTHERS)

    props = llm.seen["function"]["parameters"]["properties"]
    assert props["speaker1"]["enum"] == ["凤姐", "刘姥姥", "名单外的人", "无法判断"]


def test_k9_slots_labelled_subject_outsider_or_undecided_never_become_examples():
    _, out = _pick({"pick1": 1, "speaker1": "刘姥姥",
                    "pick2": 2, "speaker2": "名单外的人",
                    "pick3": 3, "speaker3": "无法判断",
                    "pick4": 4, "speaker4": "凤姐"}, SEVEN, "刘姥姥", [], OTHERS)

    assert out == ["凤姐：第4问，你老说呢？\n刘姥姥：第4答，姑娘说得是。"]


def test_k10_zhao_taiye_the_slot_whose_previous_line_is_his_own_is_dropped_and_a_later_slot_fills_in():
    others = [{"name": "阿Q", "aliases": []}, {"name": "赵太太", "aliases": []}]
    llm = _PickLLM({"pick1": 2, "speaker1": "赵太爷", "pick2": 1, "speaker2": "阿Q"})
    d = Distiller(llm=llm, config_path=None)
    candidates = d.dialogue_candidates(
        ZHAO, "赵太爷", ["赵大爷"], [{"name": "赵太爷", "aliases": ["赵大爷"]}, *others])

    assert [c.line for c in candidates] == ["阿Q，听说你在外面发财，", "那里会完得这样快呢？"]
    assert d.pick_dialogue_examples(candidates, "赵太爷", others) == [
        "阿Q：太爷！\n赵太爷：阿Q，听说你在外面发财，"]


def test_k11_six_slots_and_the_last_three_fill_in_when_the_first_three_are_dropped():
    llm, out = _pick({"pick1": 1, "speaker1": "刘姥姥",
                      "pick2": 2, "speaker2": "名单外的人",
                      "pick3": 3, "speaker3": "无法判断",
                      "pick4": 4, "speaker4": "凤姐",
                      "pick5": 5, "speaker5": "凤姐",
                      "pick6": 6, "speaker6": "凤姐"}, SEVEN, "刘姥姥", [], OTHERS)

    props = llm.seen["function"]["parameters"]["properties"]
    assert list(props) == [f"{k}{i}" for i in range(1, 7) for k in ("pick", "speaker")]
    assert "pick6" in llm.seen["system"], "提示词得点出全部格子名"
    assert [e.splitlines()[1] for e in out] == [
        "刘姥姥：第4答，姑娘说得是。", "刘姥姥：第5答，姑娘说得是。", "刘姥姥：第6答，姑娘说得是。"]


def test_k12_only_the_first_three_valid_slots_are_kept_in_slot_order():
    _, out = _pick({f"pick{i}": 8 - i for i in range(1, 7)}
                   | {f"speaker{i}": "凤姐" for i in range(1, 7)},
                   SEVEN, "刘姥姥", [], OTHERS)

    assert [e.splitlines()[1] for e in out] == [
        "刘姥姥：第7答，姑娘说得是。", "刘姥姥：第6答，姑娘说得是。", "刘姥姥：第5答，姑娘说得是。"]
