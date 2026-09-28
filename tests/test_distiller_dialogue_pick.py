# -*- coding: utf-8 -*-
"""`Distiller.pick_dialogue_examples`：模型只回编号，文字由代码从原文复制。

为什么单开一个文件：这条链的判据是「卡里的对话示例逐字出自原文」，不是「模型挑得好不
好」。挑得好不好无从断言；逐字与否由构造保证，而构造坏掉（拿模型返回的文字、编号越界
不校验）只会在真跑验收时以「对话示例 0/3 命中」的形态出现，届时既贵又看不出是哪一环。

**变异**（每条用例都配一个）：① 改用模型返回的 `texts` → 复制断言红；② 去掉编号校验
（按 picks 直接索引）→ 越界用例以 KeyError 形状漏出、红；③ 不判上一句有无 → 无法成对
的那条红。
"""
from __future__ import annotations

import pytest

from core.distiller import DistillError, Distiller
from core.quotes import verbatim_in
from core.schema import CharacterCard

# 刘姥姥四条候选（1..4），每条上一句都是凤姐的话：四组都能成对，好验「最多 3 组」。
CONTENT = (
    "凤姐笑道：“你老快别这样说。”\n"
    "刘姥姥笑道：“姑娘说得是。”\n"
    "凤姐道：“你老慢慢说。”\n"
    "刘姥姥叹道：“阿弥陀佛，我这一辈子也没见过这样的排场。”\n"
    "凤姐道：“你老别急。”\n"
    "刘姥姥道：“我哪敢挑姑娘的不是。”\n"
    "凤姐道：“你老说哪里话。”\n"
    "刘姥姥道：“我们乡下人，哪里懂这些。”\n"
)

GROUP2 = ("凤姐：你老慢慢说。\n"
          "刘姥姥：阿弥陀佛，我这一辈子也没见过这样的排场。")


class _PickLLM:
    """只实现 `select_by_schema` 的假适配器：记下实参，回一个固定参数。

    真适配器在这里会真发 HTTP（沙箱里还会在 httpx 建 SSL context 时偶发挂起），
    而本用例要看的只是「代码怎么用这份返回」。
    """

    def __init__(self, args: dict | None = None) -> None:
        self.args = {"picks": [2]} if args is None else args
        self.seen: dict = {}
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self._model = "fake"

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        self.seen.update(system=system_prompt, messages=messages, function=function)
        return self.args


def _pick(args: dict | None = None, content: str = CONTENT):
    llm = _PickLLM(args)
    out = Distiller(llm=llm, config_path=None).pick_dialogue_examples(
        content, "刘姥姥", [], ["凤姐"])
    return llm, out


def test_the_examples_are_copied_from_the_source_not_taken_from_the_model():
    """模型同时返回编号和文字时，只用编号，文字逐字来自原文。

    变异：改用 `args["texts"]`（模型生成文字再核对，即方案 B 那条路）→ 第一条断言红，
    而且验收时才会以「查不到」的形状现形。
    """
    llm, out = _pick({"picks": [2], "texts": ["凤姐：你老慢慢说。\n刘姥姥：阿弥陀佛……"]})

    assert out == [GROUP2]
    for line in out[0].splitlines():
        assert verbatim_in(CONTENT, line.split("：", 1)[1]), line
    assert "……" not in out[0], "省略号是模型编的形态，不该出现在复制出来的示例里"


def test_the_schema_declares_the_candidate_bounds_as_the_only_field():
    """工具 Schema 只有一个整数字段，带上限 N（array 不支持 maxItems，只能卡在单个整数上）。

    变异：去掉 `maximum` → 模型给出越界编号的概率上升，而这道限正是靠服务端校验兜住的
    那一层（非 strict 供应商下由 `valid_picks` 兜第二层）。
    """
    llm, out = _pick({"picks": [1, 2]})

    params = llm.seen["function"]["parameters"]
    assert llm.seen["function"]["name"] == "pick_dialogue_examples"
    assert params["additionalProperties"] is False, "strict 模式要求显式关闭额外字段"
    props = params["properties"]
    assert list(props) == ["picks"]
    assert props["picks"]["type"] == "array"
    assert props["picks"]["items"] == {
        "type": "integer", "minimum": 1, "maximum": 4}
    assert params["required"] == ["picks"]
    # 候选块真的发给了模型（编号与原文都在），否则它无从挑。
    assert "[2] 上一句：" in llm.seen["messages"][0]["content"]
    assert len(out) == 2


def test_out_of_range_and_duplicate_numbers_are_dropped_not_raised():
    """越界 / 重复 / 非整数的编号丢弃，剩下的照常成组。

    变异：不校验直接索引 → KeyError 从 `pick_dialogue_examples` 漏出去，调用方分不清
    是挑选失败还是代码错误（两条都该走「任务失败」，但只有前者是预期内的）。
    """
    _, out = _pick({"picks": [99, 2, "x", 2, -1]})

    assert out == [GROUP2]


def test_at_most_three_examples_are_kept():
    """挑出 4 组也只要前 3 组（与卡片的展示口径一致）。"""
    _, out = _pick({"picks": [1, 2, 3, 4]})

    assert len(out) == 3
    assert out[0].startswith("凤姐：你老快别这样说。")


def test_a_pick_without_a_previous_line_cannot_form_a_pair():
    """首句没有上一句 → 挑中也成不了组，一组都没有即失败（不落一张空示例的卡）。

    变异：不判 `prev_line` 是否存在 → 拼出「对方：\\n刘姥姥：…」这种半组，第一条断言红。
    """
    only_first = "刘姥姥笑道：“姑娘说得是。”\n凤姐道：“你老慢慢说。”\n"

    with pytest.raises(DistillError, match="对话示例"):
        _pick({"picks": [1]}, content=only_first)


def test_no_candidates_at_all_is_a_task_failure_and_the_model_is_not_called():
    """原文里没有这个角色的对话句 → 没得挑，直接失败（不花一次调用）。"""
    llm = _PickLLM({"picks": [1]})

    with pytest.raises(DistillError, match="对话示例"):
        Distiller(llm=llm, config_path=None).pick_dialogue_examples(
            "只有叙述，没有对话。", "刘姥姥", [], [])

    assert llm.seen == {}, "没候选还去问模型 = 让它在空清单上编编号"


# ── 贴到卡上（后置步骤）：三条产卡通道共用，卡上其余字段一个不动 ──────────────


def test_attach_fills_the_field_from_the_source_and_leaves_the_rest_of_the_card_alone():
    """后置步骤只改 `dialogue_examples`：模型写在卡片 JSON 里的示例被替换成原文那两行。

    变异：不做这一步（后置只当装饰）→ 卡上留着模型编的示例，第一条断言红。
    """
    d = Distiller(llm=_PickLLM({"picks": [2]}), config_path=None)
    card = CharacterCard.model_validate(
        {"name": "刘姥姥", "identity": "乡下老妪", "dialogue_examples": ["模型编的示例"]})

    out = d.attach_dialogue_examples(
        card, CONTENT, "刘姥姥", [], [{"name": "刘姥姥"}, {"name": "凤姐"}])

    assert out.dialogue_examples == [GROUP2]
    assert out.identity == "乡下老妪", "其余字段不该被这一步碰到"
    assert card.dialogue_examples == ["模型编的示例"], "原卡不就地改（调用方可能还在用）"


def test_the_subject_and_its_aliases_are_not_taken_as_the_other_speaker():
    """上一句的引导语里有两个人名时，把本人剔出去才判得出对方是谁。

    「凤姐忙和刘姥姥摆手道：」这一形态（实测约 15%）里两个名都在，说话的是凤姐。名单
    里替上一句找对方时若不排除本角色与它的别名，`speaker_in` 会同时命中两个而退化成
    「对方」—— 判据还在，只是永远给不出名字。

    变异：`other_names` 不排除本角色名/别名 → 拼出「对方：…」，第一条断言红。
    """
    d = Distiller(llm=_PickLLM({"picks": [2]}), config_path=None)
    source = ("凤姐忙和刘姥姥摆手道：“你老快别这样说。”\n"
              "刘姥姥笑道：“姑娘说得是。”\n")

    out = d.attach_dialogue_examples(
        CharacterCard(name="刘姥姥"), source, "刘姥姥", ["姥姥"],
        [{"name": "刘姥姥", "aliases": ["姥姥"]},
         {"name": "凤姐", "aliases": ["凤丫头"]}])

    assert out.dialogue_examples == ["凤姐：你老快别这样说。\n刘姥姥：姑娘说得是。"]


def test_attach_passes_the_failure_through_instead_of_saving_an_empty_field():
    """挑不出来 → 抛出，绝不返回一张示例为空的卡（空示例与「本来就没有」从成品分不出）。"""
    d = Distiller(llm=_PickLLM({"picks": [1]}), config_path=None)

    with pytest.raises(DistillError, match="对话示例"):
        d.attach_dialogue_examples(
            CharacterCard(name="刘姥姥"), "只有叙述，没有对话。", "刘姥姥")
