"""Evidence 线 commit 3：agent 透传 + 四态可辨 —— 锁与基线。

三条互不重叠的红源（范围 3 要求「不重叠才叫锁」，各锁各的）：

  A. ``test_prompt_bytes_frozen``                          —— 模型看到的字符串逐字节没变
  B. ``test_items_reach_agent_without_touching_prompt``     —— 证据侧拿到 items **且**
                                                               prompt 侧没多出任何一串
  C. ``test_four_states_are_pairwise_distinct``             —— 四态两两可辨，各一条红源

范围 2（web 改写失败）与范围 4（非 agent 路径）各有专属锁，见文件后半。

假件一律取自 ``tests/evidence_fakes.py``，不另起一套。
"""

from __future__ import annotations

import json
from typing import get_args

from core.agent.agent_loop import AgentLoop
from core.agent.tools import EMPTY_RESULT, AgentToolkit
from core.context_engine import RetrievalStatus
from core.rag import CollectionUnusableError
from core.schema import SourceStatus
from evidence_fakes import (
    DDG_ABSTRACT,
    DDG_EMPTY,
    FakeCollection,
    FakeLLM,
    FakeMemory,
    RaisingCollection,
    build_ctx,
    fake_ddg,
    make_card,
    make_rag,
)

QUERY = "莲花坞里的旧事"
HINT = "角色背景提示"


# ── 驱动 agent 路径的最小 LLM 假件 ──────────────────────────────


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, name: str, args: dict, id: str = "call_1") -> None:
        self.id = id
        self.function = _Fn(name, json.dumps(args, ensure_ascii=False))

    def model_dump(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


class _Msg:
    def __init__(self, content: str | None = None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class ToolLLM:
    """按脚本逐步请求工具（一步可多个），脚本走完即收手。

    同时把**喂给模型的每一份字符串**记下来 —— 锁 B 的运行期 spy 就是它。
    """

    def __init__(self, steps: list[list[tuple[str, dict]]]) -> None:
        self._steps = list(steps)
        self._n = 0
        self.system_prompts: list[str] = []
        self.seen_strings: list[str] = []

    def chat_with_tools(self, system_prompt: str, messages: list[dict], tools) -> _Msg:
        self.system_prompts.append(system_prompt)
        for m in messages:
            self.seen_strings.append(str(m.get("content") or ""))
        if self._n < len(self._steps):
            calls = [
                _TC(name, args, id=f"call_{self._n}_{i}")
                for i, (name, args) in enumerate(self._steps[self._n])
            ]
            self._n += 1
            return _Msg(tool_calls=calls)
        return _Msg(content="ok")


def _run(ctx, steps: list[list[tuple[str, dict]]]) -> tuple[ToolLLM, object]:
    llm = ToolLLM(steps)
    res = AgentLoop(llm, AgentToolkit(ctx)).run(HINT, [{"role": "user", "content": "hi"}])
    return llm, res


def _tool_msgs(res) -> list[str]:
    return [m["content"] for m in res.messages if m.get("role") == "tool"]


# ── 冻结基线（改动前 HEAD 的 agent 路径产出，11 个场景） ─────────
#
# 抓取方式：改动前在 HEAD 上跑同一套假件（捕获脚本 e2e/scratch/capture_agent_golden.py，
# 未入库），产出 11 个场景的 router_sp / tool 消息 / retrieved，逐字节抄到这里。
# 断言的是**模型实际读到的那串字**，不是「由 item 再渲染一次」—— 后者跟着实现一起变，
# 锁不住任何东西。

_EMPTY = EMPTY_RESULT  # "未找到相关内容"
_SCENE_HIT = "【参考原文片段（酌情使用，不要逐字复述）】\n屋顶上的旧事，风很凉。"
_MEM_HIT = (
    "【你的长期记忆——这些是你和对方之前交流中记住的事】\n"
    "- 他说过要在莲花坞种满莲花。\n- 她答应过下次带酒来。\n"
    "注意：自然地在对话中体现这些记忆，不要刻意逐条复述。"
)
_WEB_HIT = "【角色的见闻感知】\n我听说过莲花坞这个地方。"

_ROUTER_SP = (
    "你是一个对话系统的检索决策器。你的唯一职责是判断：为了让角色更好地回复用户的最新消息，"
    "是否需要检索信息。需要则调用相应工具（可多个），不需要则不调用任何工具、直接返回空内容。"
    "不要回答用户的问题本身。判断依据：对方提及过往交流或共同回忆→search_memory；"
    "涉及角色原著经历/情节→search_scenes；需要现实世界实时或事实信息→web_search；"
    "纯寒暄、情绪表达、即兴互动→不调用。\n\n【角色背景】角色背景提示"
)

# 场景名 → (模型收到的 tool 消息, 该轮 retrieved)
_GOLDEN: dict[str, tuple[list[str], list[list[str]]]] = {
    "scene_hit": ([_SCENE_HIT], [["search_scenes", _SCENE_HIT]]),
    "scene_empty": ([_EMPTY], []),
    "scene_failed": ([_EMPTY], []),
    "scene_none_rag": ([_EMPTY], []),
    "memory_hit": ([_MEM_HIT], [["search_memory", _MEM_HIT]]),
    "memory_empty": ([_EMPTY], []),
    "memory_failed": ([_EMPTY], []),
    "web_hit": ([_WEB_HIT], [["web_search", _WEB_HIT]]),
    "web_empty": ([_EMPTY], []),
    "web_rewrite_failed": ([_EMPTY], []),
    "web_failed": ([_EMPTY], []),
}

# scene_hit 里 3 条候选只活下来 1 条（RAGEngine 按 character_name 过滤 metadata.characters，
# 只有 scene_0 的 "魏无羡,江澄" 命中）—— 数字是假件与过滤规则的乘积，不是拍的。
_HIT_SHAPES = {
    "scene_hit": ("scene", "屋顶上的旧事，风很凉。", 1),
    "memory_hit": ("memory", "他说过要在莲花坞种满莲花。", 2),
    "web_hit": ("web", "莲花坞是云梦江氏的家园，以莲塘与荷风闻名。", 1),
}

_CACHE: dict[str, tuple[ToolLLM, object]] | None = None


def _scenarios() -> dict[str, tuple[ToolLLM, object]]:
    """三源 × {有命中 / 真无 / 失败} + 无 rag + web 改写失败 = 11 个场景（跑一次，缓存）。"""
    global _CACHE
    if _CACHE is None:
        out: dict[str, tuple[ToolLLM, object]] = {}

        def run(name: str, ctx, tool: str) -> None:
            out[name] = _run(ctx, [[(tool, {"query": QUERY})]])

        run("scene_hit", build_ctx(rag=make_rag(FakeCollection())), "search_scenes")
        run("scene_empty", build_ctx(rag=make_rag(FakeCollection([]))), "search_scenes")
        run(
            "scene_failed",
            build_ctx(rag=make_rag(RaisingCollection(CollectionUnusableError("集合不可用")))),
            "search_scenes",
        )
        run("scene_none_rag", build_ctx(rag=None), "search_scenes")
        run("memory_hit", build_ctx(memory=FakeMemory()), "search_memory")
        run("memory_empty", build_ctx(memory=FakeMemory(rows=[])), "search_memory")
        run(
            "memory_failed",
            build_ctx(memory=FakeMemory(raise_on_search=RuntimeError("mem0 挂了"))),
            "search_memory",
        )
        with fake_ddg(DDG_ABSTRACT):
            run("web_hit", build_ctx(llm=FakeLLM()), "web_search")
        with fake_ddg(DDG_EMPTY):
            run("web_empty", build_ctx(llm=FakeLLM()), "web_search")
        with fake_ddg(DDG_ABSTRACT):
            run(
                "web_rewrite_failed",
                build_ctx(llm=FakeLLM(raise_on_chat=RuntimeError("rate limited"))),
                "web_search",
            )
        with fake_ddg(DDG_ABSTRACT, raise_on_get=RuntimeError("网络挂了")):
            run("web_failed", build_ctx(llm=FakeLLM()), "web_search")

        _CACHE = out
    return _CACHE


# ── 锁 A：prompt 侧字节基线 ─────────────────────────────────────


def test_prompt_bytes_frozen():
    """本 commit 的硬不变量：agent 模式下喂给模型的字符串与改动前逐字节相同。

    router system prompt + 每条 tool 消息 + retrieved，三样都钉住。这不是「再渲染一遍
    比一比」—— 期望值是抄来的字面量，实现怎么改都得对上。
    """
    for name, (llm, res) in _scenarios().items():
        assert set(llm.system_prompts) == {_ROUTER_SP}, f"{name}: router system prompt 变了"
        exp_msgs, exp_retrieved = _GOLDEN[name]
        assert _tool_msgs(res) == exp_msgs, f"{name}: tool 消息变了"
        assert [list(t) for t in res.retrieved] == exp_retrieved, f"{name}: retrieved 变了"
        assert res.degraded is False, name


# ── 锁 B：items 到了 agent 层，且没进 prompt ────────────────────


def test_items_reach_agent_without_touching_prompt():
    """范围 3 的两半必须同时成立，缺一不成锁：

      1. 证据侧：命中的三源把 items 送到了 agent 层（非空、kind 对齐、原文而非渲染块）；
      2. prompt 侧：运行期 spy —— 模型这一轮看到的**每一个**字符串，只能是「用户原话 /
         助手空内容 / 冻结的 tool 消息」三者之一，没有第四条路让证据结构进模型视野。

    只锁 1 会漏掉「items 混进 prompt」，只锁 2 会漏掉「items 根本没传过来」——
    2b 的教训正是「声称锁住三条分支，实际只锁了两条」。
    """
    scenarios = _scenarios()

    for name, (source, first_text, n_items) in _HIT_SHAPES.items():
        _llm, res = scenarios[name]
        assert len(res.evidence) == 1, name
        tr = res.evidence[0]
        assert (tr.source, tr.status) == (source, "hit"), name
        assert len(tr.items) == n_items, name
        # item 存的是**检索原文**，不是渲染好的块 —— 这条把它们分开
        assert tr.items[0].text == first_text, name
        assert tr.items[0].kind == source, name
        assert tr.items[0].meta, f"{name}: meta 为空，解释字段丢了"

    for name, (llm, res) in scenarios.items():
        assert set(llm.system_prompts) == {_ROUTER_SP}, name
        # 模型视野的完整刻画：用户原话 / 助手条目内容（含 tool_calls 的空 content）/
        # 冻结的 tool 结果。多出任何一串就是证据侧漏进了 prompt。
        allowed = {"hi", "", *_GOLDEN[name][0]}
        assert [s for s in llm.seen_strings if s not in allowed] == [], name


# ── 锁 C：四态两两可辨，每态一条专属红源 ────────────────────────


def _scene_status(monkeypatch, ctx, timeout: int | None = None):
    if timeout is not None:
        monkeypatch.setattr(AgentToolkit, "SCENE_TIMEOUT", timeout)
    return AgentToolkit(ctx).execute("search_scenes", {"query": QUERY})


def test_four_states_are_pairwise_distinct(monkeypatch):
    """命中 / 真无 / 失败 / 超时 —— 四态两两可辨，逐条列红源（互不重叠）：

      hit     ← FakeCollection(SCENE_ROWS)                   status="hit" + items 非空
      empty   ← FakeCollection([])                            status="empty"
      failed  ← RaisingCollection(CollectionUnusableError)    status="failed"
      timeout ← FakeCollection(sleep_s=2.5) + SCENE_TIMEOUT=1 status="timeout"

    变异对照（跑过、还原，见 commit message）：超时合成态改 "failed" → 只 timeout 红；
    ``_retrieve_via`` 的 not items 分支改 "failed" → 只 empty 红；异常降级改 "empty" →
    只 failed 红。三条红源落在三个不同代码块，互不遮蔽。
    """
    got = {
        "hit": _scene_status(monkeypatch, build_ctx(rag=make_rag(FakeCollection()))),
        "empty": _scene_status(monkeypatch, build_ctx(rag=make_rag(FakeCollection([])))),
        "failed": _scene_status(
            monkeypatch,
            build_ctx(rag=make_rag(RaisingCollection(CollectionUnusableError("炸了")))),
        ),
        "timeout": _scene_status(
            monkeypatch, build_ctx(rag=make_rag(FakeCollection(sleep_s=2.5))), timeout=1
        ),
    }

    assert {k: v.trace.status for k, v in got.items()} == {
        "hit": "hit", "empty": "empty", "failed": "failed", "timeout": "timeout",
    }
    assert {v.trace.source for v in got.values()} == {"scene"}  # 来源词汇统一走 EvidenceKind
    assert [v.ok for v in got.values()] == [True, False, False, False]

    # 要害：prompt 侧 empty 与 failed 的文案**完全一样**，光看喂给模型的字符串根本分不开
    # —— 这正是本轮要补的盲点，可分性只存在于 trace。（timeout 文案不同，是调用层的旁证。）
    assert got["empty"].content == got["failed"].content == EMPTY_RESULT
    assert got["timeout"].content != EMPTY_RESULT
    assert got["hit"].content == _SCENE_HIT


# ── 范围 2 的定死：web 改写失败不改变 status ────────────────────


def test_web_rewrite_failure_keeps_hit_status():
    """范围 2 选 (b)：前端**不为** web 改写失败做区分，故 status 不动。

    理由：status 的定义是「检索」三态，改写属于块生成 —— 让一个字段同时描述两件事正是
    本仓缺陷 25 的形态。前端显示的是 items（检索到了什么），改写失败时 items 非空、显示
    「检索来源:1」是真话；块空不空是 prompt 侧的事，不该由证据侧字段承担。

    本测试钉住这个决定：改写失败时 status 仍是 hit。将来若有人以为它能区分改写成败并
    据此改前端，这条会红。
    """
    with fake_ddg(DDG_ABSTRACT):
        ctx = build_ctx(llm=FakeLLM(raise_on_chat=RuntimeError("rate limited")))
        res = AgentToolkit(ctx).execute("web_search", {"query": QUERY})

    assert res.trace.status == "hit"                     # 检索成功（第一阶段真跑通了）
    assert len(res.trace.items) == 1                     # 来源确实检索到了
    assert res.trace.items[0].text == DDG_ABSTRACT["AbstractText"]
    assert res.trace.items[0].meta["url"] == DDG_ABSTRACT["AbstractURL"]
    assert res.content == EMPTY_RESULT                   # 但块是空的（改写/过滤没产出）
    assert res.ok is False                               # ok 由块决定，不由 status


# ── 累积 / 去重 / 顺序 ─────────────────────────────────────────


def test_evidence_accumulation_dedup_and_order():
    """同一轮多次调用：按调用顺序累积；重复 (工具,参数) 不重复记；空/失败**照样记**。

    「空结果与失败不许被累积成『没检索过』」是本范围的硬要求：前端少显示一条无从察觉，
    显示一条失败却一眼可见。故证据侧收下全部真实调用，不被 ok 过滤。
    """
    with fake_ddg(DDG_ABSTRACT, raise_on_get=RuntimeError("网络挂了")):
        ctx = build_ctx(rag=make_rag(FakeCollection()), memory=FakeMemory(rows=[]))
        _llm, res = _run(ctx, [
            [("search_scenes", {"query": "a"}), ("search_memory", {"query": "b"})],
            [("search_scenes", {"query": "a"}), ("web_search", {"query": "c"}),
             ("no_such_tool", {"query": "d"})],
        ])

    assert [(t.source, t.status) for t in res.evidence] == [
        ("scene", "hit"),
        ("memory", "empty"),   # 真无匹配：记下来，不许当成「没检索过」
        ("web", "failed"),     # 检索失败：同样记下来
    ]
    # 重复 (search_scenes, a) 第二次没发生检索 → 不产生第二条 trace
    assert [t.source for t in res.evidence].count("scene") == 1
    # 未知工具 = 压根没检索 → 不入证据（仍在 steps 里留痕）
    assert len(res.evidence) == 3
    assert len(res.steps) == 4
    # 证据侧与 retrieved 是两个口径：retrieved 只收「ok 且块非空」
    assert [t[0] for t in res.retrieved] == ["search_scenes"]


# ── 漂移锁：两个 Literal 的包含关系 ─────────────────────────────


def test_source_status_superset_of_retrieval_status():
    """SourceStatus ⊇ RetrievalStatus，多的恰好是 timeout（调用层专属）。

    两个 Literal 分居两处、两个语义：引擎侧是「检索说了什么」三态，调用侧再加一态
    「这次调用没在预算内回来」。把 timeout 塞进引擎类型 = 让引擎承诺一个它永远产不出的
    值（死分支，本仓缺陷 27 同型）。本锁钉住包含关系，动任一侧都会红。
    """
    engine_side = set(get_args(RetrievalStatus))
    call_side = set(get_args(SourceStatus))
    assert engine_side < call_side
    assert call_side - engine_side == {"timeout"}


def test_timeout_is_produced_only_in_tools_layer():
    """源码级：``status="timeout"`` 全仓只在 core/agent/tools.py 出现。

    超时是**弃船**这一调用层事实，只有工具执行器产得出。别处冒出这个字面量，说明有人
    把调用层语义搬进了引擎层。
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    hits = [
        p.relative_to(root).as_posix()
        for p in (root / "core").rglob("*.py")
        if 'status="timeout"' in p.read_text(encoding="utf-8")
    ]
    assert hits == ["core/agent/tools.py"], hits


# ── 范围 4：非 agent 路径也透出 items ───────────────────────────


def test_build_ex_exposes_traces_and_build_stays_a_thin_delegate():
    """非 agent 路径（前端默认 agentMode=false、群聊恒走这条）也透出 items。

    ``build()`` 是 ``build_ex().prompt`` 的薄委托 —— 单一构造路径，不是第二条渲染分支。
    """
    ctx = build_ctx(rag=make_rag(FakeCollection()), memory=FakeMemory())
    built = ctx.build_ex(QUERY, "对方")

    assert [t.source for t in built.traces] == ["scene", "memory"]  # web 开关默认关
    assert [t.status for t in built.traces] == ["hit", "hit"]
    assert [len(t.items) for t in built.traces] == [1, 2]
    assert [t.items[0].kind for t in built.traces] == ["scene", "memory"]
    # 字符串出口与结构出口同源：同参数再 build 一次，prompt 一致
    assert ctx.build(QUERY, "对方") == built.prompt

    # include_dynamic=False（agent 模式的固定区构建）→ 不检索、无 trace
    assert ctx.build_ex(QUERY, "对方", include_dynamic=False).traces == []


def test_chat_engine_last_traces_wired_on_both_paths():
    """两条出口都落 ``last_traces``：非 agent 走 ``_compose_context``，agent 走 evidence。

    落一处漏一处，前端就会静默拿到上一轮的旧证据 —— 比拿不到更糟（看起来是正常的）。
    """
    from core.chat_engine import ChatEngine

    eng = ChatEngine(
        llm=ToolLLM([]),
        rag=make_rag(FakeCollection()),
        card=make_card(),
        memory_manager=FakeMemory(),
        card_id="card_test",
    )

    # 非 agent：_compose_context（_compose_system_prompt 与 group_session 共用它）
    eng._compose_context(QUERY)
    assert [t.source for t in eng.last_traces] == ["scene", "memory"]
    assert all(t.status == "hit" for t in eng.last_traces)
    assert eng.last_traces[0].items[0].text == "屋顶上的旧事，风很凉。"

    # agent：_run_agent_phase 用 evidence 覆盖，且最终 system prompt 里带了检索参考块
    eng.llm = ToolLLM([[("search_scenes", {"query": QUERY})]])
    final_sp, _msgs = eng._run_agent_phase(
        "角色背景", [{"role": "user", "content": "hi"}], QUERY
    )
    assert [t.source for t in eng.last_traces] == ["scene"]
    assert "【检索参考】" in final_sp
    # 证据侧带原文，prompt 侧带渲染块 —— 两者不许互相取材
    assert eng.last_traces[0].items[0].text == "屋顶上的旧事，风很凉。"
    assert _SCENE_HIT in final_sp
