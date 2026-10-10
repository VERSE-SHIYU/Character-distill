# -*- coding: utf-8 -*-
"""`Distiller.pick_dialogue_examples`：模型只回编号与上一句说话人，文字由代码从原文复制。

为什么单开一个文件：这条链的判据是「卡里的对话示例逐字出自原文、对方名来自名单」，不是
「模型挑得好不好」。挑得好不好无从断言；逐字与否由构造保证，而构造坏掉（拿模型返回的
文字、编号越界不校验、说话人按引导语里的人名猜）只会在真跑验收时以「对话示例 0/3 命中」
或「对方名错」的形态出现，届时既贵又看不出是哪一环。

**变异**（每条用例都配一个）：① 改用模型返回的 `texts` → 复制断言红；② 去掉编号/说话人
校验 → 越界或编造的项漏进卡；③ 说话人改回按引导语子串取名（`speaker_in`）→ 茄鲞那条红；
④ `attach` 内绕开 `dialogue_candidates` 直接抽取 → 取候选的规则就有了第二份；
⑤ 挑选 schema 改回变长数组 → 结构断言红（宝玉就是被无界输出截断的）；⑥ 不丢编号 0 →
「含 0 就少一组」那条红。
"""
from __future__ import annotations

import pytest

from core.distiller import DistillError, Distiller
from core.quotes import PICK_SLOTS, Candidate, verbatim_in
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
        self.args = ({"pick1": 2, "speaker1": "凤姐"}
                     if args is None else args)
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
    llm, out = _pick({"pick1": 2, "speaker1": "凤姐",
                      "texts": ["凤姐：你老慢慢说。\n刘姥姥：阿弥陀佛……"]})

    assert out == [GROUP2]
    for line in out[0].splitlines():
        assert verbatim_in(CONTENT, line.split("：", 1)[1]), line
    assert "……" not in out[0], "省略号是模型编的形态，不该出现在复制出来的示例里"


def test_the_schema_is_fixed_slots_not_an_unbounded_array():
    """工具 Schema：挑选结果是 `PICK_SLOTS` 个固定槽位（`pick{i}` integer 0..N + `speaker{i}`
    enum），不是变长数组 —— 数组长度 strict 拦不住，无界输出会在候选多时把回复撑爆（宝玉
    1672 条候选那次就是 `finish_reason=length` 截断）。

    `speaker{i}` 的 enum 是 `SpeakerOptions.enum`：名单里其他人物的标准名 + 本角色标准名 +
    「名单外的人」+「无法判断」（顺序如此）。别名不进 enum（同一人两个选项），本角色标准名
    进 enum 是为了让模型如实标出「上一句是本人说的」，选到即丢；`pick{i}` 的 `maximum` 是
    候选数，`minimum` 是 0（该格不挑）。

    变异：改回 `picks` 数组 → `properties` 不是固定槽位字段、结构断言红；enum 改回只有
    「其他人 + 无法判断」（漏了本人／名单外的人）→ enum 断言红；去掉 `maximum` → 越界靠
    服务端拦不住（非 strict 供应商下由 `valid_picks` 兜第二层）。
    """
    llm, out = _pick()

    f = llm.seen["function"]
    assert f["name"] == "pick_dialogue_examples"
    params = f["parameters"]
    assert params["type"] == "object"
    assert params["additionalProperties"] is False, "strict 模式要求显式关闭额外字段"
    # PICK_SLOTS 个格子、每格一个整数字段 + 一个 enum 字段，数量由 PICK_SLOTS 生成。
    assert PICK_SLOTS == 6
    assert list(params["properties"]) == [
        "pick1", "speaker1", "pick2", "speaker2", "pick3", "speaker3",
        "pick4", "speaker4", "pick5", "speaker5", "pick6", "speaker6"]
    assert params["required"] == list(params["properties"]), "strict 要求属性全部必填"
    for i in range(1, PICK_SLOTS + 1):
        assert params["properties"][f"pick{i}"] == {
            "type": "integer", "minimum": 0, "maximum": 4}
        assert params["properties"][f"speaker{i}"]["type"] == "string"
        assert params["properties"][f"speaker{i}"]["enum"] == [
            "凤姐", "刘姥姥", "名单外的人", "无法判断"]
    # 名单（标准名 + 别名）真的发给了模型：原文写「凤丫头」时它得能对到「凤姐」。
    assert "凤姐" in llm.seen["system"] and "凤丫头" in llm.seen["system"]
    # 提示词点明了格子名与「不足填 0」，否则模型不知道往哪填、也不知道空位怎么处理。
    assert "pick1" in llm.seen["system"] and "填 0" in llm.seen["system"]
    # 候选块真的发给了模型（编号、原文片段、两句都在），否则它无从读片段判定。
    assert "[2] 片段：" in llm.seen["messages"][0]["content"]
    assert "本句：" in llm.seen["messages"][0]["content"]
    assert len(out) == 1


def test_three_valid_slots_make_three_examples_in_slot_order():
    """三格都填了有效编号 → 三组示例，顺序就是格子的顺序（模型自己排的序）。"""
    _, out = _pick({"pick1": 1, "speaker1": "凤姐",
                    "pick2": 2, "speaker2": "凤姐",
                    "pick3": 3, "speaker3": "凤姐"})

    assert len(out) == 3
    assert out[0].startswith("凤姐：你老快别这样说。")   # 1 号候选
    assert out[2].startswith("凤姐：你老别急。")         # 3 号候选


def test_a_zero_slot_means_fewer_examples():
    """编号 0 = 这一格不挑 → 示例少于 MAX_EXAMPLES 组。

    变异：不丢 0（`by_n[0]` 直接索引）→ KeyError 从 `pick_dialogue_examples` 漏出去，
    调用方分不清是挑选失败还是代码错误 → 本断言红。
    """
    _, out = _pick({"pick1": 1, "speaker1": "凤姐",
                    "pick2": 0, "speaker2": "凤姐",
                    "pick3": 3, "speaker3": "凤姐"})

    assert len(out) == 2
    assert out[0].startswith("凤姐：你老快别这样说。")
    assert out[1].startswith("凤姐：你老别急。")


def test_out_of_range_duplicate_undecided_and_off_enum_picks_are_dropped_not_raised():
    """越界 / 重复 / 非整数 / 「无法判断」/ 不在 enum 里的名字 / 缺字段 —— 一律丢，剩下的照常成组。

    变异：不校验直接索引 → KeyError 从 `pick_dialogue_examples` 漏出去，调用方分不清是
    挑选失败还是代码错误；接受「无法判断」→ 拼出没有对方名的组。
    """
    _, out = _pick({"pick1": 99, "speaker1": "凤姐",       # 越界
                    "pick2": 2, "speaker2": "凤姐",
                    "pick3": "3", "speaker3": "凤姐"})      # 非整数
    assert out == [GROUP2]

    _, out2 = _pick({"pick1": 2, "speaker1": "凤姐",
                     "pick2": 2, "speaker2": "薛宝钗",      # 重复编号，第二格丢
                     "pick3": 1, "speaker3": "无法判断"})   # 判不准，第三格丢
    assert out2 == [GROUP2]

    _, out3 = _pick({"pick1": 1, "speaker1": "薛宝钗",     # 说话人不在 enum，第一格丢
                     "pick2": 4, "speaker2": "凤姐"})       # 第三格缺字段，不算错
    assert out3 == ["凤姐：你老说哪里话。\n刘姥姥：我们乡下人，哪里懂这些。"]


def test_no_candidates_at_all_is_a_task_failure_and_the_model_is_not_called():
    """原文里没有这个角色的对话句 → 没得挑，直接失败（不花一次调用）。

    提示里要点出「认的引号是哪些」：失败最常见的原因是版本用了别的引号（本轮不支持 ASCII
    引号），看不出这一点就只能猜。
    """
    llm = _PickLLM({"pick1": 1, "speaker1": "凤姐"})

    with pytest.raises(DistillError, match="找不到「刘姥姥」的对话句"):
        Distiller(llm=llm, config_path=None).dialogue_candidates(
            "只有叙述，没有对话。", "刘姥姥", [], OTHERS)

    assert llm.seen == {}, "没候选还去问模型 = 让它在空清单上编编号"


def test_a_roster_with_nobody_but_the_subject_fails_before_the_model_is_called():
    """名单里除本角色外没有别人 → enum 没有可选的对方，成不了组，在调用模型之前就抛。

    本角色自己的别名也一并排除：名单里只剩他这一行（标准名 + 别名）仍是「没有别人」。
    变异：不查这一条 → 付完一次调用才在挑选处发现一组都成不了对。
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
    d = Distiller(llm=_PickLLM({"pick1": 2, "speaker1": "凤姐"}),
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

    _, out = _pick({"pick1": 1, "speaker1": "凤姐"}, content=source)

    assert out == ["凤姐：你尝尝这个。\n刘姥姥：姑娘说得是。"]


def test_the_subjects_alias_is_out_of_the_enum_but_the_standard_name_is_in():
    """本角色的**别名**不进 enum（同一人两个选项）；标准名进 enum 是为了让模型如实标出
    「上一句是本人说的」。选到本人的格子被丢弃由目标检查 K9、K10 守，不在本条。

    变异：别名也进 enum → 名单里「刘姥姥／姥姥」会各成一个选项，enum 断言红；标准名
    不进 enum → enum 断言红。
    """
    llm = _PickLLM({"pick1": 1, "speaker1": "凤姐"})
    source = ('凤姐忙和刘姥姥摆手道：“你老快别这样说。”\n'
              '刘姥姥笑道：“姑娘说得是。”\n')

    out = Distiller(llm=llm, config_path=None).attach_dialogue_examples(
        CharacterCard(name="刘姥姥"), source, "刘姥姥", ["姥姥"],
        [{"name": "刘姥姥", "aliases": ["姥姥"]},
         {"name": "凤姐", "aliases": ["凤丫头"]}])

    assert out.dialogue_examples == ["凤姐：你老快别这样说。\n刘姥姥：姑娘说得是。"]
    props = llm.seen["function"]["parameters"]["properties"]
    assert props["speaker1"]["enum"] == ["凤姐", "刘姥姥", "名单外的人", "无法判断"]


def test_attach_passes_the_failure_through_instead_of_saving_an_empty_field():
    """挑不出来 → 本方法照实抛出，原因在异常里；保底（照常出卡）是 `finalize_card` 的事。"""
    d = Distiller(llm=_PickLLM({"pick1": 1, "speaker1": "凤姐"}),
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
    """`attach` 的候选只从 `dialogue_candidates` 取：换掉它，`attach` 的产出跟着换。

    `content` 与替身给的候选是两段不同的话，产出哪一段就说明 `attach` 走的是哪条路 ——
    取候选的规则只此一处，`attach` 不另抽一遍。
    变异：`attach` 内改为直接调 `extract_candidates`（绕开 `dialogue_candidates`）→ 示例
    来自 `content`，断言红。
    """
    d = _FixedCandidates(_PickLLM({"pick1": 1, "speaker1": "凤姐"}),
                         config_path=None)

    out = d.attach_dialogue_examples(
        CharacterCard(name="刘姥姥"),
        '凤姐道：“别的话。”\n刘姥姥道：“另一句。”\n',
        "刘姥姥", [], [{"name": "刘姥姥"}, {"name": "凤姐"}])

    assert out.dialogue_examples == [
        "凤姐：你老慢慢说。\n刘姥姥：我这一辈子也没见过这样的排场。"]
