# -*- coding: utf-8 -*-
"""`Distiller.pick_dialogue_examples`：模型只回编号与上一句说话人，文字由代码从原文复制。

为什么单开一个文件：这条链的判据是「卡里的对话示例逐字出自原文、对方名来自名单」，不是
「模型挑得好不好」。挑得好不好无从断言；逐字与否由构造保证，而构造坏掉（拿模型返回的
文字、编号越界不校验、说话人按引导语里的人名猜）只会在真跑验收时以「对话示例 0/3 命中」
或「对方名错」的形态出现，届时既贵又看不出是哪一环。

**变异**（每条用例都配一个）：① 改用模型返回的 `texts` → 复制断言红；② 去掉编号/说话人
校验 → 越界或编造的项漏进卡；③ 说话人改回按引导语子串取名（`speaker_in`）→ 茄鲞那条红；
④ `attach` 内绕开 `dialogue_candidates` 直接抽取 → 预检放行的与挑选看到的不是同一批。
"""
from __future__ import annotations

import pytest

from core.distiller import DistillError, Distiller
from core.quotes import Candidate, verbatim_in
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

OTHERS = [{"name": "凤姐", "aliases": ["凤丫头"]}]


class _PickLLM:
    """只实现 `select_by_schema` 的假适配器：记下实参，回一个固定参数。

    真适配器在这里会真发 HTTP（沙箱里还会在 httpx 建 SSL context 时偶发挂起），
    而本用例要看的只是「代码怎么用这份返回」。
    """

    def __init__(self, args: dict | None = None) -> None:
        self.args = {"picks": [{"n": 2, "prev_speaker": "凤姐"}]} if args is None else args
        self.seen: dict = {}
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self._model = "fake"

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        self.seen.update(system=system_prompt, messages=messages, function=function)
        return self.args


def _pick(args: dict | None = None, content: str = CONTENT, others=OTHERS):
    llm = _PickLLM(args)
    d = Distiller(llm=llm, config_path=None)
    candidates = d.dialogue_candidates(content, "刘姥姥", [], others)
    return llm, d.pick_dialogue_examples(candidates, "刘姥姥", others)


def test_the_examples_are_copied_from_the_source_not_taken_from_the_model():
    """模型同时返回编号和文字时，只用编号，文字逐字来自原文。

    变异：改用 `args["texts"]`（模型生成文字再核对，即方案 B 那条路）→ 第一条断言红，
    而且验收时才会以「查不到」的形状现形。
    """
    llm, out = _pick({"picks": [{"n": 2, "prev_speaker": "凤姐"}],
                      "texts": ["凤姐：你老慢慢说。\n刘姥姥：阿弥陀佛……"]})

    assert out == [GROUP2]
    for line in out[0].splitlines():
        assert verbatim_in(CONTENT, line.split("：", 1)[1]), line
    assert "……" not in out[0], "省略号是模型编的形态，不该出现在复制出来的示例里"


def test_the_schema_takes_the_other_peoples_standard_names_plus_undecided():
    """工具 Schema：`picks` 是对象数组，`n` 卡上限 N，`prev_speaker` 的 enum 是名单里
    其他人物的标准名再加「无法判断」—— 别名不进 enum（同一人两个选项）。

    变异：enum 改回含本角色或别名 → 第一条 enum 断言红；去掉 `n` 的 `maximum` → 越界编号
    靠服务端拦不住（非 strict 供应商下由 `valid_picks` 兜第二层）。
    """
    llm, out = _pick()

    f = llm.seen["function"]
    assert f["name"] == "pick_dialogue_examples"
    params = f["parameters"]
    assert params["additionalProperties"] is False, "strict 模式要求显式关闭额外字段"
    assert list(params["properties"]) == ["picks"]
    item = params["properties"]["picks"]["items"]
    assert item["type"] == "object"
    assert item["additionalProperties"] is False
    assert item["required"] == ["n", "prev_speaker"]
    assert item["properties"]["n"] == {"type": "integer", "minimum": 1, "maximum": 4}
    assert item["properties"]["prev_speaker"]["enum"] == ["凤姐", "无法判断"]
    assert params["required"] == ["picks"]
    # 名单（标准名 + 别名）真的发给了模型：原文写「凤丫头」时它得能对到「凤姐」。
    assert "凤姐" in llm.seen["system"] and "凤丫头" in llm.seen["system"]
    # 候选块真的发给了模型（编号、原文片段、两句都在），否则它无从读片段判定。
    assert "[2] 片段：" in llm.seen["messages"][0]["content"]
    assert "本句：" in llm.seen["messages"][0]["content"]
    assert len(out) == 1


def test_out_of_range_duplicate_undecided_and_off_enum_picks_are_dropped_not_raised():
    """越界 / 重复 / 非整数 / 「无法判断」/ 不在 enum 里的名字 / 缺字段 —— 一律丢，剩下的照常成组。

    变异：不校验直接索引 → KeyError 从 `pick_dialogue_examples` 漏出去，调用方分不清是
    挑选失败还是代码错误；接受「无法判断」→ 拼出没有对方名的组。
    """
    _, out = _pick({"picks": [
        {"n": 99, "prev_speaker": "凤姐"},
        {"n": 2, "prev_speaker": "凤姐"},
        {"n": 2, "prev_speaker": "凤姐"},
        {"n": 1, "prev_speaker": "无法判断"},
        {"n": 3, "prev_speaker": "薛宝钗"},
        {"n": "4", "prev_speaker": "凤姐"},
        {"n": 4},
        "不是对象",
        None,
    ]})

    assert out == [GROUP2]


def test_at_most_three_examples_are_kept():
    """挑出 4 组也只要前 3 组（与卡片的展示口径一致）。"""
    _, out = _pick({"picks": [{"n": n, "prev_speaker": "凤姐"} for n in (1, 2, 3, 4)]})

    assert len(out) == 3
    assert out[0].startswith("凤姐：你老快别这样说。")


def test_no_candidates_at_all_is_a_task_failure_and_the_model_is_not_called():
    """原文里没有这个角色的对话句 → 没得挑，直接失败（不花一次调用）。

    提示里要点出「认的引号是哪些」：失败最常见的原因是版本用了别的引号（本轮不支持 ASCII
    引号），看不出这一点就只能猜。
    """
    llm = _PickLLM({"picks": [{"n": 1, "prev_speaker": "凤姐"}]})

    with pytest.raises(DistillError, match="找不到「刘姥姥」的对话句"):
        Distiller(llm=llm, config_path=None).dialogue_candidates(
            "只有叙述，没有对话。", "刘姥姥", [], OTHERS)

    assert llm.seen == {}, "没候选还去问模型 = 让它在空清单上编编号"


def test_a_roster_with_nobody_but_the_subject_fails_before_the_model_is_called():
    """名单里除本角色外没有别人 → enum 没有可选的对方，成不了组，同样在花钱前失败。

    本角色自己的别名也一并排除：名单里只剩他这一行（标准名 + 别名）仍是「没有别人」。
    变异：不查这一条 → 预检放行，付完一次调用才在挑选处发现一组都成不了对。
    """
    llm = _PickLLM()

    with pytest.raises(DistillError, match="没有别人"):
        Distiller(llm=llm, config_path=None).dialogue_candidates(
            CONTENT, "刘姥姥", ["姥姥"],
            [{"name": "刘姥姥", "aliases": ["姥姥"]}])

    assert llm.seen == {}, "没有可当对方的人还去问模型 = 让它编一个不存在的对方名"


# ── 贴到卡上（后置步骤）：三条产卡通道共用，卡上其余字段一个不动 ──────────────


def test_attach_fills_the_field_from_the_source_and_leaves_the_rest_of_the_card_alone():
    """后置步骤只改 `dialogue_examples`：模型写在卡片 JSON 里的示例被替换成原文那两行。

    变异：不做这一步（后置只当装饰）→ 卡上留着模型编的示例，第一条断言红。
    """
    d = Distiller(llm=_PickLLM({"picks": [{"n": 2, "prev_speaker": "凤姐"}]}),
                  config_path=None)
    card = CharacterCard.model_validate(
        {"name": "刘姥姥", "identity": "乡下老妪", "dialogue_examples": ["模型编的示例"]})

    out = d.attach_dialogue_examples(
        card, CONTENT, "刘姥姥", [], [{"name": "刘姥姥"}, {"name": "凤姐"}])

    assert out.dialogue_examples == [GROUP2]
    assert out.identity == "乡下老妪", "其余字段不该被这一步碰到"
    assert card.dialogue_examples == ["模型编的示例"], "原卡不就地改（调用方可能还在用）"


def test_the_previous_speaker_is_the_models_choice_not_a_name_found_in_the_lead():
    """上一句的引导语里出现本角色的名字，也不代表这句话是他说的（名字常是宾语）。

    「依言搛些茄鲞送入刘姥姥口中，因笑道」——说话的是凤姐。旧的按子串取名（`speaker_in`）
    在这里找不到「别人」的名字，会退化成「对方：…」；新做法由模型读整段片段给出「凤姐」。

    变异：改回按引导语里的人名取名 → 第一条断言红。
    """
    source = ('依言搛些茄鲞送入刘姥姥口中，因笑道：“你尝尝这个。”\n'
              '刘姥姥笑道：“姑娘说得是。”\n')

    _, out = _pick({"picks": [{"n": 1, "prev_speaker": "凤姐"}]}, content=source)

    assert out == ["凤姐：你尝尝这个。\n刘姥姥：姑娘说得是。"]


def test_the_subject_and_its_aliases_are_kept_out_of_the_enum():
    """名单里本角色那一行（标准名与别名）不进 enum：否则模型可能选出「自己接自己」。

    变异：enum 不排除本角色/别名 → 名单里「刘姥姥／姥姥」会各自成为一个选项，断言红。
    """
    llm = _PickLLM({"picks": [{"n": 1, "prev_speaker": "凤姐"}]})
    source = ('凤姐忙和刘姥姥摆手道：“你老快别这样说。”\n'
              '刘姥姥笑道：“姑娘说得是。”\n')

    out = Distiller(llm=llm, config_path=None).attach_dialogue_examples(
        CharacterCard(name="刘姥姥"), source, "刘姥姥", ["姥姥"],
        [{"name": "刘姥姥", "aliases": ["姥姥"]},
         {"name": "凤姐", "aliases": ["凤丫头"]}])

    assert out.dialogue_examples == ["凤姐：你老快别这样说。\n刘姥姥：姑娘说得是。"]
    enum = llm.seen["function"]["parameters"]["properties"]["picks"]["items"][
        "properties"]["prev_speaker"]["enum"]
    assert enum == ["凤姐", "无法判断"]


def test_attach_passes_the_failure_through_instead_of_saving_an_empty_field():
    """挑不出来 → 抛出，绝不返回一张示例为空的卡（空示例与「本来就没有」从成品分不出）。"""
    d = Distiller(llm=_PickLLM({"picks": [{"n": 1, "prev_speaker": "凤姐"}]}),
                  config_path=None)

    with pytest.raises(DistillError, match="对话示例"):
        d.attach_dialogue_examples(
            CharacterCard(name="刘姥姥"), "只有叙述，没有对话。", "刘姥姥")


class _FixedCandidates(Distiller):
    """把 `dialogue_candidates` 换成替身：返回的是哪批候选，一眼看得出有没有被用上。"""

    FIXED = [Candidate(n=1, lead="", line="我这一辈子也没见过这样的排场。",
                       prev_lead="", prev_line="你老慢慢说。", context="")]

    def dialogue_candidates(self, content, name, aliases=(), roster=()):
        return list(self.FIXED)


def test_attach_takes_its_candidates_from_dialogue_candidates_not_from_the_text_again():
    """预检与挑选是同一个取候选的方法：换掉它，`attach` 的产出跟着换。

    `content` 与替身给的候选是两段不同的话，产出哪一段就说明 `attach` 走的是哪条路 ——
    预检与挑选一旦分家，「预检过了挑选就不会因为没候选而失败」这句就不成立。
    变异：`attach` 内改为直接调 `extract_candidates`（绕开 `dialogue_candidates`）→ 示例
    来自 `content`，断言红。
    """
    d = _FixedCandidates(_PickLLM({"picks": [{"n": 1, "prev_speaker": "凤姐"}]}),
                         config_path=None)

    out = d.attach_dialogue_examples(
        CharacterCard(name="刘姥姥"),
        '凤姐道：“别的话。”\n刘姥姥道：“另一句。”\n',
        "刘姥姥", [], [{"name": "刘姥姥"}, {"name": "凤姐"}])

    assert out.dialogue_examples == [
        "凤姐：你老慢慢说。\n刘姥姥：我这一辈子也没见过这样的排场。"]
