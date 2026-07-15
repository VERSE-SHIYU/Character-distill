#!/usr/bin/env python3
"""Agent 工具调用循环离线评测脚本。

用法:
  python scripts/run_agent_eval.py --card_id <card_id>
  python scripts/run_agent_eval.py --card_id <card_id> --modes agent
  python scripts/run_agent_eval.py --card_id <card_id> --cases tests/eval/custom_cases.jsonl

依赖：
  - 项目环境（pip install -r requirements.txt）
  - 已配置 config.yaml 或环境变量 DEEPSEEK_API_KEY
  - 已配置 STORAGE_BACKEND（SQLite 或 Postgres，用于加载角色卡）
  - 管理员 LLM（用于 eval 决策）

输出：
  - eval_report.md   — 汇总报告
  - eval_raw.jsonl   — 原始明细（每行一个 CaseResult）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

# ── 项目根路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── 项目依赖 ──
from adapters.llm_adapter import LLMAdapter, ToolsNotSupportedError
from core.agent.agent_loop import AgentLoop
from core.agent.tools import AgentToolkit
from core.chat_engine import ChatEngine
from core.context_engine import _count_tokens
from core.rag import RAGEngine
from core.schema import CharacterCard
from storage import get_store

# ── 路径 ──
DEFAULT_CASES = PROJECT_ROOT / "tests" / "eval" / "agent_eval_cases.jsonl"
DEFAULT_REPORT = PROJECT_ROOT / "eval_report.md"
DEFAULT_RAW = PROJECT_ROOT / "eval_raw.jsonl"

# ── 用于 memory 类的 5 条预置记忆 ──
SEED_MEMORIES: list[dict[str, str]] = [
    {"user": "我特别喜欢吃辣", "assistant": "记住了，你喜欢吃辣。"},
    {"user": "我养了一只猫叫团团", "assistant": "团团，好可爱的名字。"},
    {"user": "我最近在准备考研", "assistant": "加油，你一定可以的。"},
    {"user": "我最喜欢周杰伦的《七里香》", "assistant": "那首歌很好听。"},
    {"user": "我上周去了海边", "assistant": "海边一定很美吧。"},
]


# ── 数据结构 ──


@dataclass
class CaseResult:
    """单条评测结果。"""
    case_id: str
    category: str
    mode: str
    sp_tokens: int
    sp_text_len: int
    steps: int
    tool_names: list[str] = field(default_factory=list)
    tool_msg_tokens: int = 0
    reply: str = ""
    reply_truncated: str = ""
    error: str | None = None
    usage_prompt: int = 0
    usage_completion: int = 0
    keyword_hit: bool | None = None


# ── 指标采集辅助 ──


class BuildMetrics:
    """通过替换 ContextEngine.build 来采集 system prompt 大小。"""

    def __init__(self) -> None:
        self.last_sp_tokens = 0
        self.last_sp_len = 0
        self._original: Any = None

    def install(self, engine: ChatEngine) -> None:
        ctx = engine._ctx_engine
        self._original = ctx.build

        def wrapped(user_message, user_role="", current_mood=None, include_dynamic=True):
            result = self._original(user_message, user_role, current_mood, include_dynamic)
            self.last_sp_tokens = _count_tokens(result)
            self.last_sp_len = len(result)
            return result

        ctx.build = wrapped

    def uninstall(self, engine: ChatEngine) -> None:
        if self._original is not None:
            engine._ctx_engine.build = self._original


def compute_tool_msg_tokens(messages: list[dict]) -> int:
    """计算 tool 回填消息的总 token 数。"""
    return sum(
        _count_tokens(m.get("content", ""))
        for m in messages
        if m.get("role") == "tool"
    )


def keyword_in_reply(reply: str, keyword: str | None) -> bool:
    """检查回复中是否包含期望关键词。"""
    if keyword is None:
        return True  # 无可验证关键词时视为通过
    return keyword.lower() in reply.lower()


# ── 种子记忆 ──


async def seed_memories(mem_mgr: Any, card_id: str) -> bool:
    """向 memory manager 灌入固定评测记忆。失败时返回 False。"""
    if mem_mgr is None:
        return False
    try:
        for pair in SEED_MEMORIES:
            if hasattr(mem_mgr, "add"):
                messages = [
                    {"role": "user", "content": pair["user"]},
                    {"role": "assistant", "content": pair["assistant"]},
                ]
                if asyncio.iscoroutinefunction(mem_mgr.add):
                    await mem_mgr.add(messages, card_id, metadata={"importance": 10, "eval_seed": True})
                else:
                    mem_mgr.add(messages, card_id, metadata={"importance": 10, "eval_seed": True})
        return True
    except Exception as exc:
        print(f"[eval] Memory seeding failed (non-fatal): {exc}")
        return False


# ── 单条执行 ──


async def run_case(
    case: dict,
    mode: str,
    llm: LLMAdapter,
    card: CharacterCard,
) -> CaseResult:
    """执行单条评测用例，返回 CaseResult。"""
    case_id = case["id"]
    category = case["category"]
    expected = case.get("expected_tools", [])
    keyword = case.get("expected_keyword")

    # 全新 engine（不复用 history）
    engine = ChatEngine(
        llm=llm,
        rag=None,
        card=card,
        memory_manager=None,   # eval 中不依赖外部 memory manager
        card_id=f"eval-{uuid4().hex[:8]}",
    )
    engine._session_id = f"eval-{uuid4().hex[:12]}"
    engine.agent_mode = (mode == "agent")

    # 装 metrics 采集
    metrics = BuildMetrics()
    metrics.install(engine)

    try:
        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(None, engine.chat, case["input"])

        sp_tokens = metrics.last_sp_tokens
        sp_text_len = metrics.last_sp_len

        # Agent 模式下抓 tool 调用情况
        if engine.agent_mode:
            # AgentLoop 在内部被调用，我们无法直接拿到 result 对象。
            # 通过检查 engine.history 判断：agent 模式下 tool 消息不进 history，
            # 但 llm_messages 的副本中有。所以只能根据最后一步 chat_with_tools
            # 的调用结果间接判断。这里改用浅层观察：agent_mode 时，
            # _run_agent_phase 已经完成，但无法拿到内部 steps。
            # 替代方案：用 ApproximateSignal，从回复本身推断工具是否被调用。
            tool_names = []
            steps_count = 0
            tool_msg_tok = 0
        else:
            tool_names = []
            steps_count = 0
            tool_msg_tok = 0

        # legacy 模式下 legcy ，拼接 tool 消息均无，全部为零。
        # 那么结果字段只能填充外部的已知信息，内部细节缺失。
        #
        # 这一点是在设计上留的缺口：要精确统计 agent 模式的 tool 行为，
        # 必须在外层包装 AgentLoop。有两种实现方针：
        # (a) 定义外层 wrap_agent_loop
        # (b) 重新简化，直接让这个函数能拿到 AgentLoopResult

        # 决定采用方针(b)：我们在外部自己调用 AgentLoop，
        # 而不是通过 engine.chat()。但在 agent 模式下 engine.chat()
        # 内部已经调用了 _run_agent_phase。如果同步再从外面调一次，
        # 会重复调用。
        #
        # 正确方案：对 agent 模式不调 engine.chat()，
        # 而是手动拼 system_prompt → 构建 llm_messages → 手动调 AgentLoop，
        # 然后自己调 LLM 做最终生成。
        #
        # 这样 metric 采集完全自控。

        # 但这违背了"不改生产代码"原则——我们其实是用 eval 脚本
        # 复现了 engine.chat() 的局部逻辑。
        #
        # 权衡后采用更简单的折中：
        # agent 模式也调 engine.chat()，但对于 agent 的 steps 采集，
        # 我们重新初始化一个 AgentLoop 并 mock 它的 run 方法，
        # 让被 monkeypatch 的 run 同时记录指标并转发给真实 run。

        # 实际上，这个函数太复杂了，直接全部重写为上层调度逻辑，
        # 下层 run_single_case 做实际执行。重构到__main__的 run_cases 中。

        # 最终方案：两遍调用。
        # legacy: engine.chat() 直接调，采集 sp 指标
        # agent:  手动处理（后面 run_cases 中分支处理）

        # 但这里已经是 run_case，需要统一返回 CaseResult。
        # 为了让架构简洁，这里只做 legacy 模式：
        if mode != "legacy":
            raise NotImplementedError("agent mode handled in run_cases")

        return CaseResult(
            case_id=case_id,
            category=category,
            mode=mode,
            sp_tokens=sp_tokens,
            sp_text_len=sp_text_len,
            steps=0,
            tool_msg_tokens=0,
            reply=reply,
            reply_truncated=reply[:200],
            usage_prompt=llm.last_usage.get("prompt_tokens", 0) if llm.last_usage else 0,
            usage_completion=llm.last_usage.get("completion_tokens", 0) if llm.last_usage else 0,
            keyword_hit=keyword_in_reply(reply, keyword) if category == "realtime" and keyword else None,
        )
    except Exception as exc:
        return CaseResult(
            case_id=case_id,
            category=category,
            mode=mode,
            sp_tokens=0,
            sp_text_len=0,
            steps=0,
            error=str(exc),
        )
    finally:
        metrics.uninstall(engine)


# ── 评测主流程 ──


async def run_cases(
    cases: list[dict],
    modes: list[str],
    llm: LLMAdapter,
    card: CharacterCard,
    mem_mgr: Any,
    card_id: str,
) -> list[CaseResult]:
    """全量执行评测，返回结果列表。"""
    results: list[CaseResult] = []
    total = len(cases) * len(modes)
    done = 0

    # 预置记忆（失败则静默降级）
    mem_seeded = await seed_memories(mem_mgr, card_id) if mem_mgr else False
    if mem_seeded:
        print(f"[eval] Seeded {len(SEED_MEMORIES)} memories for evaluation.")

    # 逐条逐模式执行
    for case in cases:
        case_id = case["id"]
        category = case["category"]
        expected = case.get("expected_tools", [])
        keyword = case.get("expected_keyword")

        # ── legacy 模式 ──
        if "legacy" in modes:
            r = await _run_legacy(case, llm, card, expected, keyword)
            results.append(r)
            done += 1
            _print_progress(done, total, r)

        # ── agent 模式 ──
        if "agent" in modes:
            r = await _run_agent(case, llm, card, expected, keyword)
            results.append(r)
            done += 1
            _print_progress(done, total, r)

    print(f"\n[eval] Done. {len(results)} results collected.")
    return results


def _print_progress(done: int, total: int, r: CaseResult) -> None:
    flag = "OK" if r.error is None else "ERR"
    print(f"  [{done}/{total}] {r.mode:6s} {r.case_id:15s} sp={r.sp_tokens:5d}tok steps={r.steps} {flag}")


async def _run_legacy(
    case: dict, llm: LLMAdapter, card: CharacterCard,
    expected: list[str], keyword: str | None,
) -> CaseResult:
    """Legacy 模式：engine.chat() → 采集 system prompt 指标。"""
    engine = ChatEngine(llm=llm, rag=None, card=card, card_id=f"eval-{uuid4().hex[:8]}")
    engine._session_id = f"eval-{uuid4().hex[:12]}"
    engine.agent_mode = False

    metrics = BuildMetrics()
    metrics.install(engine)

    try:
        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(None, engine.chat, case["input"])

        result = CaseResult(
            case_id=case["id"],
            category=case["category"],
            mode="legacy",
            sp_tokens=metrics.last_sp_tokens,
            sp_text_len=metrics.last_sp_len,
            steps=0,
            reply=reply,
            reply_truncated=reply[:200],
            usage_prompt=llm.last_usage.get("prompt_tokens", 0) if llm.last_usage else 0,
            usage_completion=llm.last_usage.get("completion_tokens", 0) if llm.last_usage else 0,
            keyword_hit=keyword_in_reply(reply, keyword) if keyword else None,
        )
        return result
    except Exception as exc:
        return CaseResult(
            case_id=case["id"], category=case["category"],
            mode="legacy", sp_tokens=0, sp_text_len=0, steps=0, error=str(exc),
        )
    finally:
        metrics.uninstall(engine)


async def _run_agent(
    case: dict, llm: LLMAdapter, card: CharacterCard,
    expected: list[str], keyword: str | None,
) -> CaseResult:
    """Agent 模式：手动 build → AgentLoop → LLM 最终生成，全指标采集。"""
    engine = ChatEngine(llm=llm, rag=None, card=card, card_id=f"eval-{uuid4().hex[:8]}")
    engine._session_id = f"eval-{uuid4().hex[:12]}"
    engine.agent_mode = True

    metrics = BuildMetrics()
    metrics.install(engine)

    try:
        # Step 1: 构建 system prompt + llm_messages（复用 engine 内部方法）
        # 用 monkeypatch 风格覆盖 engine.chat() 的 system prompt 构建阶段
        msg = case["input"]

        # 调用 _compose_system_prompt （不调 engine.chat()，手动拆步骤）
        # 注意：engine._compose_system_prompt 需要 self.mood, self.user_role 等
        # 但新建的 engine 有默认的 self._mood = "平静", self.user_role = ""
        system_prompt = engine._compose_system_prompt(
            msg, voice_mode=False, include_dynamic=False,
        )
        sp_tokens_before = metrics.last_sp_tokens

        llm_messages = engine._build_llm_messages(engine.history, msg)

        # time_block（engine.chat 中的逻辑）
        time_block = engine._build_time_awareness_block()
        if time_block and llm_messages and llm_messages[-1]["role"] == "user":
            llm_messages[-1] = {
                **llm_messages[-1],
                "content": llm_messages[-1]["content"] + time_block,
            }

        # Step 2: AgentLoop 决策
        toolkit = AgentToolkit(engine._ctx_engine, current_mood=engine._mood)
        loop_result = AgentLoop(llm, toolkit).run(system_prompt, llm_messages)
        if loop_result.degraded:
            sp_tokens = sp_tokens_before
            tool_msg_tok = compute_tool_msg_tokens(loop_result.messages)
            # fallback → legacy reply
            legacy_sp = engine._compose_system_prompt(msg, voice_mode=False, include_dynamic=True)
            loop_reply = await asyncio.get_running_loop().run_in_executor(
                None, llm.chat, legacy_sp, llm_messages,
            )
            return CaseResult(
                case_id=case["id"], category=case["category"],
                mode="agent",
                sp_tokens=_count_tokens(legacy_sp),
                sp_text_len=len(legacy_sp),
                steps=0,
                reply=loop_reply,
                reply_truncated=loop_reply[:200],
                usage_prompt=llm.last_usage.get("prompt_tokens", 0) if llm.last_usage else 0,
                usage_completion=llm.last_usage.get("completion_tokens", 0) if llm.last_usage else 0,
                keyword_hit=keyword_in_reply(loop_reply, keyword) if keyword else None,
            )

        steps_meta = loop_result.steps
        llm_messages = loop_result.messages

        sp_tokens = sp_tokens_before
        tool_msg_tok = compute_tool_msg_tokens(llm_messages)
        tool_names = list(dict.fromkeys(s["tool"] for s in steps_meta))

        # Step 3: 最终回答（不带 tools）
        loop_reply = await asyncio.get_running_loop().run_in_executor(
            None, llm.chat, system_prompt, llm_messages,
        )

        return CaseResult(
            case_id=case["id"], category=case["category"],
            mode="agent",
            sp_tokens=sp_tokens,
            sp_text_len=0,
            steps=len(steps_meta),
            tool_names=tool_names,
            tool_msg_tokens=tool_msg_tok,
            reply=loop_reply,
            reply_truncated=loop_reply[:200],
            usage_prompt=llm.last_usage.get("prompt_tokens", 0) if llm.last_usage else 0,
            usage_completion=llm.last_usage.get("completion_tokens", 0) if llm.last_usage else 0,
            keyword_hit=keyword_in_reply(loop_reply, keyword) if keyword else None,
        )
    except Exception as exc:
        return CaseResult(
            case_id=case["id"], category=case["category"],
            mode="agent", sp_tokens=0, sp_text_len=0, steps=0, error=str(exc),
        )
    finally:
        metrics.uninstall(engine)


# ── 判定 ──


def judge_trigger(r: CaseResult, expected: list[str]) -> bool:
    """判定工具触发是否正确。

    chitchat 类：steps=0 → 正确。
    memory/realtime 类：steps 中的工具名与 expected 有交集 → 正确。
    """
    if r.error:
        return False
    if not expected:
        return r.steps == 0
    return any(t in expected for t in r.tool_names)


# ── 报告输出 ──


def write_report(
    results: list[CaseResult],
    report_path: Path,
    cases: list[dict],
    mem_seeded: bool,
) -> None:
    """生成 Markdown 评测报告。"""
    # 构建查询索引
    case_map = {c["id"]: c for c in cases}
    by_mode: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_mode[r.mode].append(r)

    lines: list[str] = [
        "# Agent 工具调用循环评测报告\n",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n",
        f"用例总数：{len(cases)}（chitchat×{sum(1 for c in cases if not c.get('expected_tools'))} "
        f"memory×{sum(1 for c in cases if any(t in ('search_memory','search_scenes') for t in c.get('expected_tools',[])))} "
        f"realtime×{sum(1 for c in cases if 'web_search' in c.get('expected_tools',[]))}）\n",
        f"记忆预置：{'✓' if mem_seeded else '✗（内存降级，仅验证工具触发）'}\n",
        "",
    ]

    # ── 1. 触发准确率 ──
    lines.append("## 一、工具触发准确率\n")
    lines.append("| 类别 | 模式 | 正确 | 总数 | 准确率 |")
    lines.append("|------|------|------|------|--------|")

    categories = ["chitchat", "memory", "realtime"]
    for cat in categories:
        for mode in sorted(by_mode):
            group = [r for r in by_mode[mode] if r.category == cat]
            if not group:
                continue
            expected = []
            if cat == "memory":
                expected = ["search_memory", "search_scenes"]
            elif cat == "realtime":
                expected = ["web_search"]
            correct = sum(1 for r in group if judge_trigger(r, expected))
            total = len(group)
            rate = f"{correct}/{total}"
            lines.append(f"| {cat} | {mode} | {correct} | {total} | {rate} |")

    lines.append("")
    lines.append("> 准确率定义：chitchat→无工具调用；memory→命中 search_memory/search_scenes；")
    lines.append("> realtime→命中 web_search。\n")

    # ── 2. Token 对比 ──
    lines.append("## 二、System Prompt Token 对比\n")
    lines.append("| 类别 | 模式 | 平均注入 token | 中位注入 token | 样本量 |")
    lines.append("|------|------|----------------|----------------|--------|")

    for cat in categories:
        for mode in sorted(by_mode):
            group = [r for r in by_mode[mode] if r.category == cat and r.sp_tokens > 0]
            if not group:
                continue
            tokens = [r.sp_tokens for r in group]
            avg = sum(tokens) / len(tokens)
            sorted_t = sorted(tokens)
            median = sorted_t[len(sorted_t) // 2]
            lines.append(f"| {cat} | {mode} | {avg:.0f} | {median} | {len(group)} |")

    lines.append("")
    chitchat_legacy = [r for r in by_mode.get("legacy", []) if r.category == "chitchat" and r.sp_tokens > 0]
    chitchat_agent = [r for r in by_mode.get("agent", []) if r.category == "chitchat" and r.sp_tokens > 0]
    if chitchat_legacy and chitchat_agent:
        l_avg = sum(r.sp_tokens for r in chitchat_legacy) / len(chitchat_legacy)
        a_avg = sum(r.sp_tokens for r in chitchat_agent) / len(chitchat_agent)
        saving = l_avg - a_avg
        pct = (saving / l_avg * 100) if l_avg > 0 else 0
        lines.append(f"> **chitchat 类重点**：legacy 平均注入 {l_avg:.0f}tok → agent 平均注入 {a_avg:.0f}tok，")
        lines.append(f"> 每条节约 {saving:.0f}tok（{pct:.1f}%）。\n")

    # ── 3. 事实性对比 ──
    lines.append("## 三、事实性（realtime 静态 key 命中）\n")
    lines.append("| 模式 | 命中 | 总数 | 命中率 |")
    lines.append("|------|------|------|--------|")

    static_ids = {c["id"] for c in cases if c["category"] == "realtime" and c.get("expected_keyword")}
    for mode in sorted(by_mode):
        group = [r for r in by_mode[mode] if r.case_id in static_ids and r.keyword_hit is not None]
        if not group:
            continue
        hits = sum(1 for r in group if r.keyword_hit)
        total = len(group)
        lines.append(f"| {mode} | {hits} | {total} | {hits/total*100:.0f}% |")

    lines.append("")

    # ── 4. 累计 Token 成本 ──
    lines.append("## 四、累计 Token 消耗\n")
    lines.append("| 模式 | Prompt Token | Completion Token | 总 Token |")
    lines.append("|------|-------------|-----------------|---------|")
    for mode in sorted(by_mode):
        prompt_total = sum(r.usage_prompt for r in by_mode[mode])
        comp_total = sum(r.usage_completion for r in by_mode[mode])
        lines.append(f"| {mode} | {prompt_total} | {comp_total} | {prompt_total + comp_total} |")

    lines.append("")

    # ── 5. 错误记录 ──
    errors = [r for r in results if r.error]
    if errors:
        lines.append("## 五、异常记录\n")
        lines.append("| case_id | mode | error |")
        lines.append("|---------|------|-------|")
        for e in errors:
            lines.append(f"| {e.case_id} | {e.mode} | {e.error} |")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[eval] Report written to {report_path}")


def write_raw(results: list[CaseResult], raw_path: Path) -> None:
    """输出原始明细 JSONL。"""
    with raw_path.open("w", encoding="utf-8") as f:
        for r in results:
            d = asdict(r)
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"[eval] Raw data written to {raw_path}")


# ── 加载卡 ──


async def load_card(card_id: str) -> CharacterCard:
    """从存储加载角色卡。"""
    storage = get_store()
    # SQLiteStore.get_card 是 async
    card_rec = await storage.get_card(card_id)
    if not card_rec:
        print(f"Card {card_id} not found in storage.")
        sys.exit(1)
    card = CharacterCard.model_validate_json(card_rec["card_json"])
    print(f"[eval] Loaded card: {card.name} (id={card_id})")
    return card


# ── 加载评测用例 ──


def load_cases(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        print(f"Cases file not found: {p}")
        sys.exit(1)
    cases: list[dict] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    print(f"[eval] Loaded {len(cases)} cases from {p}")
    return cases


# ── 入口 ──


async def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 工具调用循环离线评测")
    parser.add_argument("--card_id", required=True, help="角色卡 ID")
    parser.add_argument(
        "--modes", default="legacy,agent",
        help='评测模式，逗号分隔（legacy,agent），默认两个都跑',
    )
    parser.add_argument(
        "--cases", default=str(DEFAULT_CASES),
        help=f"评测用例 JSONL 路径（默认 {DEFAULT_CASES}）",
    )
    parser.add_argument(
        "--report", default=str(DEFAULT_REPORT),
        help=f"报告输出路径（默认 {DEFAULT_REPORT}）",
    )
    parser.add_argument(
        "--raw", default=str(DEFAULT_RAW),
        help=f"原始数据输出路径（默认 {DEFAULT_RAW}）",
    )
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    print(f"[eval] Starting agent evaluation: card_id={args.card_id} modes={modes}")

    # 初始化
    llm = LLMAdapter()
    card = await load_card(args.card_id)
    cases = load_cases(args.cases)

    # Memory seeding（若有可用的 memory manager）
    mem_mgr = None
    try:
        from core.memory_manager import MemoryManager
        mem_mgr = MemoryManager(db_path=os.getenv("MEM0_DB_PATH", "data/mem0.db"))
    except Exception as exc:
        print(f"[eval] MemoryManager unavailable (non-fatal): {exc}")
        mem_mgr = None
    mem_seeded = await seed_memories(mem_mgr, args.card_id) if mem_mgr else False

    # 执行
    results = await run_cases(cases, modes, llm, card, mem_mgr, args.card_id)

    # 输出
    write_report(results, Path(args.report), cases, mem_seeded)
    write_raw(results, Path(args.raw))


if __name__ == "__main__":
    asyncio.run(main())
