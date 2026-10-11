# -*- coding: utf-8 -*-
"""对话示例「片段从哪开始」目标检查（docs/specs/dialogue-context-window.md §1）。

现状（main 6a10ab7）：交给模型的片段从上一句的引导语往前最多 60 字，而且不越过再前一个引号。
上一句写的是「他说」「自己摇头说」时，「他」是谁写在更前面，片段里看不到；第一人称小说里
模型就把这句话标成「我」说的（四次实测 7 次，全在这一类）。

目标：上一句的说话人只要在它之前 10 句以内被点了名，模型拿到的片段里就看得见这个名字；
片段变长之后，长书主角交给模型的东西仍在它一次能读的量以内。

走真实代码：`Distiller.dialogue_candidates` / `pick_dialogue_examples`（模型用
`test_dialogue_pairing_goal` 里那个假适配器）、
`core.quotes.extract_candidates`。预言独立于被测代码：W1–W3、W6 的原文是鲁迅公版原文
（一字未改），期望是人读原文得出的；W4、W5 是只为拎出一条规则的构造文本。

候选有上限（段 1；长书主角的候选上千条，片段变长后全交出去会超过模型一次能读的量）
C1 100 条候选直接递给挑选这一步 → 交给模型 40 条：schema 里编号的上限是 40，首尾两条都在
C2 取的是全书均匀的 40 条：出场顺序不变，相邻两条隔 2–3 条
C3 模型按编号挑中的，取回来的就是它看到的那一条（main 上就绿：回归守卫）
C4 不多于 40 条的原样交出去（main 上就绿：回归守卫）

看得见是谁（段 2）
W1 《孔乙己》上一句「他说，」→ 片段里它之前有「孔乙己」；这段片段原样进了发给模型的提示
W2 《孔乙己》上一句「自己摇头说，」→ 片段里它之前有「孔乙己」
W3 《故乡》上一句「他的态度终于恭敬起来了，分明的叫道：」→ 片段里它之前有「闰土」
带多少
W4 上一句之前恰好带 10 句：第 21 句在、第 20 句不在
W5 往前数到的位置落在一句台词中间 → 退到那句台词的引导语开头，片段不从半句话开始
没动别的（main 上就绿：回归守卫）
W6 候选还是原来那几条：本句、上一句都不变
W7 片段仍是原文的连续子串，仍到本句的署名为止
W8 片段有界：不用「。！？」断句的长文本，上一句之前最多带 2000 字（main 上是 60 字，也绿）
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.distiller import DistillError, Distiller
from core.quotes import extract_candidates
from test_dialogue_pairing_goal import _pick, _PickLLM   # 同一步的假适配器，不另抄一份

# ── 鲁迅公版原文（一字未改）────────────────────────────────────────────────
# 《孔乙己》：孔乙己对「我」连说两句，后一句的引导语只写「他说，」。
KONG_ASK = (
    "在这些时候，我可以附和着笑，掌柜是决不责备的。而且掌柜见了孔乙己，也每每这样问他，引人发"
    "笑。孔乙己自己知道不能和他们谈天，便只好向孩子说话。有一回对我说道，“你读过书么？”我略"
    "略点一点头。他说，“读过书，……我便考你一考。茴香豆的茴字，怎样写的？”我想，讨饭一样的"
    "人，也配考我么？便回过脸去，不再理会。孔乙己等了许久，很恳切的说道，“不能写罢？……我教"
    "给你，记着！这些字应该记着。将来做掌柜的时候，写账要用。”"
)
# 《孔乙己》：孔乙己对孩子们说的后一句，引导语是「直起身又看一看豆，自己摇头说，」。
KONG_BEANS = (
    "有几回，邻居孩子听得笑声，也赶热闹，围住了孔乙己。他便给他们茴香豆吃，一人一颗。孩子吃完"
    "豆，仍然不散，眼睛都望着碟子。孔乙己着了慌，伸开五指将碟子罩住，弯腰下去说道，“不多了，"
    "我已经不多了。”直起身又看一看豆，自己摇头说，“不多不多！多乎哉？不多也。”于是这一群孩"
    "子都在笑声里走散了。\n"
    "孔乙己是这样的使人快活，可是没有他，别人也便这么过。\n"
    "有一天，大约是中秋前的两三天，掌柜正在慢慢的结账，取下粉板，忽然说，“孔乙己长久没有来了"
    "。还欠十九个钱呢！”我才也觉得他的确长久没有来了。"
)
# 《故乡》：闰土进门，「这来的便是闰土。」之后一直用「他」。
GUXIANG_VISIT = (
    "一日是天气很冷的午后，我吃过午饭，坐着喝茶，觉得外面有人进来了，便回头去看。我看时，不由"
    "的非常出惊，慌忙站起身，迎着走去。\n"
    "这来的便是闰土。虽然我一见便知道是闰土，但又不是我这记忆上的闰土了。他身材增加了一倍；先"
    "前的紫色的圆脸，已经变作灰黄，而且加上了很深的皱纹；眼睛也像他父亲一样，周围都肿得通红，"
    "这我知道，在海边种地的人，终日吹着海风，大抵是这样的。他头上是一顶破毡帽，身上只一件极薄"
    "的棉衣，浑身瑟索着；手里提着一个纸包和一支长烟管，那手也不是我所记得的红活圆实的手，却又"
    "粗又笨而且开裂，像是松树皮了。\n"
    "我这时很兴奋，但不知道怎么说才好，只是说：\n"
    "“阿！闰土哥，——你来了？……”\n"
    "我接着便有许多话，想要连珠一般涌出：角鸡，跳鱼儿，贝壳，猹，……但又总觉得被什么挡着似的"
    "，单在脑里面回旋，吐不出口外去。\n"
    "他站住了，脸上现出欢喜和凄凉的神情；动着嘴唇，却没有作声。他的态度终于恭敬起来了，分明的"
    "叫道：\n"
    "“老爷！……”\n"
    "我似乎打了一个寒噤；我就知道，我们之间已经隔了一层可悲的厚障壁了。我也说不出话。\n"
    "他回过头去说，“水生，给老爷磕头。”便拖出躲在背后的孩子来，这正是一个廿年前的闰土，只是"
    "黄瘦些，颈子上没有银圈罢了。"
)

# ── 构造文本（只为把一条规则单独拎出来）────────────────────────────────────
# 凤姐与刘姥姥一问一答：7 轮、100 轮。刘姥姥的候选分别是 7 条、100 条。
def _rounds(n):
    return "".join(f"凤姐道：“第{i}问，你老说呢？”\n刘姥姥道：“第{i}答，姑娘说得是。”\n"
                   for i in range(1, n + 1))


SEVEN, HUNDRED = _rounds(7), _rounds(100)
FENG = [{"name": "凤姐", "aliases": []}]
# 上一句之前有 30 句叙述。
THIRTY = ("".join(f"第{i}句叙述。" for i in range(1, 31))
          + "凤姐笑道：“你老慢慢说。”\n刘姥姥道：“姑娘说得是。”\n")
# 上一句之前是贾母一口气说的 15 句话：往前数 10 句，落在这句台词中间。
LONG_LINE = ("贾母道：“" + "".join(f"第{i}件事。" for i in range(1, 16)) + "”\n"
             + "凤姐笑道：“你老慢慢说。”\n刘姥姥道：“姑娘说得是。”\n")


def _only(text, names):
    cands = extract_candidates(text, names)
    assert len(cands) == 1, [c.line for c in cands]
    return cands[0]


def _before_prev(c):
    """片段里「上一句」开引号之前的部分（含它的引导语）。"""
    return c.context[:c.context.index("“" + c.prev_line)]


# ── 候选有上限 ─────────────────────────────────────────────────────────────

def _shown(llm):
    """模型看到的每条候选：`(编号, 本句是第几答)`，按出现顺序。"""
    return [(int(n), int(k)) for n, k in re.findall(
        r"\[(\d+)\] 片段：.*?本句：刘姥姥道：“第(\d+)答", llm.seen["messages"][0]["content"], re.S)]


def test_c1_a_hundred_candidates_become_forty_and_both_ends_are_kept():
    # 候选直接递给挑选这一步（不经 `dialogue_candidates`）：上限守在交给模型的地方，谁递进来都一样。
    llm = _PickLLM({"pick1": 1, "speaker1": "凤姐"})
    Distiller(llm=llm, config_path=None).pick_dialogue_examples(
        extract_candidates(HUNDRED, ["刘姥姥"]), "刘姥姥", FENG)

    shown = _shown(llm)
    assert [n for n, _ in shown] == list(range(1, 41)), "编号从 1 连到 40"
    assert llm.seen["function"]["parameters"]["properties"]["pick1"]["maximum"] == 40
    assert shown[0][1] == 1 and shown[-1][1] == 100, "全书第一条和最后一条都在"


def test_c2_the_forty_are_spread_evenly_in_story_order():
    llm, _ = _pick({"pick1": 1, "speaker1": "凤姐"}, HUNDRED, "刘姥姥", [], FENG)

    answers = [k for _, k in _shown(llm)]
    gaps = {b - a for a, b in zip(answers, answers[1:])}
    assert gaps <= {2, 3}, "100 条取 40 条，相邻两条该隔 2–3 条"


def test_c3_the_line_copied_back_is_the_one_the_model_saw_under_that_number():
    llm, out = _pick({"pick1": 2, "speaker1": "凤姐", "pick2": 40, "speaker2": "凤姐"}, HUNDRED, "刘姥姥", [], FENG)

    by_number = dict(_shown(llm))
    assert out == [f"凤姐：第{k}问，你老说呢？\n刘姥姥：第{k}答，姑娘说得是。"
                   for k in (by_number[2], by_number[40])]


def test_c4_forty_or_fewer_candidates_are_passed_on_untouched():
    llm, _ = _pick({"pick1": 1, "speaker1": "凤姐"}, SEVEN, "刘姥姥", [], FENG)

    assert _shown(llm) == [(i, i) for i in range(1, 8)]


# ── 看得见是谁 ─────────────────────────────────────────────────────────────

def test_w1_kong_yiji_he_said_the_fragment_names_him_and_reaches_the_model():
    others = [{"name": "掌柜", "aliases": []}, {"name": "我", "aliases": []}]
    llm = _PickLLM({"pick1": 1, "speaker1": "孔乙己"})
    d = Distiller(llm=llm, config_path=None)
    cands = d.dialogue_candidates(KONG_ASK, "孔乙己", [], [{"name": "孔乙己"}, *others])

    assert [c.prev_lead for c in cands] == ["他说，"]
    assert "孔乙己" in _before_prev(cands[0]), "「他」是谁，片段里得看得见"

    with pytest.raises(DistillError):   # 上一句标成本人 → 这一格被丢弃、没有示例：与本条无关
        d.pick_dialogue_examples(cands, "孔乙己", others)
    assert cands[0].context in llm.seen["messages"][0]["content"], "模型拿到的就是这段片段"


def test_w2_kong_yiji_shaking_his_head_the_fragment_names_him():
    c = _only(KONG_BEANS, ["掌柜"])

    assert c.prev_lead == "直起身又看一看豆，自己摇头说，"
    assert "孔乙己" in _before_prev(c)


def test_w3_runtu_his_manner_turned_respectful_the_fragment_names_him():
    c = _only(GUXIANG_VISIT, ["闰土"])

    assert c.prev_lead == "他的态度终于恭敬起来了，分明的叫道："
    assert "闰土" in _before_prev(c)


# ── 带多少 ─────────────────────────────────────────────────────────────────

def test_w4_exactly_ten_sentences_before_the_previous_line():
    c = _only(THIRTY, ["刘姥姥"])

    assert c.context.startswith("第21句叙述。")
    assert "第20句叙述。" not in c.context


def test_w5_a_start_inside_a_spoken_line_backs_up_to_that_lines_lead():
    c = _only(LONG_LINE, ["刘姥姥"])

    assert c.context.startswith("贾母道：“第1件事。"), "不从半句台词开始，那句话是谁说的也带上"
    assert c.context.count("“") == c.context.count("”")


# ── 没动别的 ───────────────────────────────────────────────────────────────

def test_w6_the_candidates_themselves_are_unchanged():
    kong = _only(KONG_ASK, ["孔乙己"])
    assert kong.prev_line.startswith("读过书，……我便考你一考。")
    assert kong.line.startswith("不能写罢？……我教给你，记着！")

    boss = _only(KONG_BEANS, ["掌柜"])
    assert boss.prev_line == "不多不多！多乎哉？不多也。"
    assert boss.line == "孔乙己长久没有来了。还欠十九个钱呢！"

    runtu = _only(GUXIANG_VISIT, ["闰土"])
    assert runtu.prev_line == "老爷！……"
    assert runtu.line == "水生，给老爷磕头。"


def test_w7_the_fragment_is_still_a_slice_of_the_source_ending_at_the_signature():
    for text, names, ending in (
            (KONG_ASK, ["孔乙己"], "将来做掌柜的时候，写账要用。”"),
            (KONG_BEANS, ["掌柜"], "我才也觉得他的确长久没有来了。"),
            (GUXIANG_VISIT, ["闰土"], "颈子上没有银圈罢了。")):
        c = _only(text, names)
        assert c.context in text
        assert c.context.endswith(ending)


def test_w8_the_fragment_is_bounded_when_the_text_has_no_sentence_ends():
    # 半角标点的文本数不到「。！？」：片段不能把全文开头到上一句整段带上。
    text = "他又说了一句话. " * 2000 + "\n凤姐笑道：“你老慢慢说。”\n刘姥姥道：“姑娘说得是。”\n"
    c = _only(text, ["刘姥姥"])

    assert len(_before_prev(c)) <= 2000 + len("凤姐笑道：")

