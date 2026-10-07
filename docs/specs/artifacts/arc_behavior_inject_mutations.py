"""变异预跑（spec `arc-behavior-inject.md` §7 对账表 M1–M24 + 目标检查 G-*）。

逐条把改后代码改坏一处（放宽、过严两个方向），对应测试必须红；跑完逐字节还原。

用法：在仓库根目录
    .venv/Scripts/python.exe docs/specs/artifacts/arc_behavior_inject_mutations.py

执行框架与判档复用 `tests/perf/mutation_framework.py` 与 `tests/lock_coverage.py`（同
`arc_phase_unlocated_mutations.py`）。一次性对账，**不**登记进覆盖闭合元锁。

退出码：0 = 全部红；1 = 有存活；2 = 基线红（拒跑）。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "perf"))
sys.path.insert(0, str(ROOT / "tests"))

import lock_coverage  # noqa: E402
import mutation_framework as framework  # noqa: E402

VIEW = ROOT / "core" / "arc_view.py"
CTX = ROOT / "core" / "context_engine.py"
CHAT = ROOT / "core" / "chat_engine.py"
GROUP = ROOT / "core" / "group_session.py"
TARGETS = (VIEW, CTX, CHAT, GROUP)

T = "tests/test_arc_behavior_inject.py"
GOAL = "tests/test_arc_behavior_inject_goal.py"


def _t(name: str) -> str:
    return f"{T}::{name}"


_OWN = ("    own = [b.model_copy(deep=True) for b in card.character_arc.phases[k - 1].behaviors]"
        " if k >= 1 else []\n")
_MERGE = "    proj.situation_behaviors = own + list(proj.situation_behaviors)\n"
_SECTIONS = '    ("遇事的做法", lambda c: behavior_lines(c.situation_behaviors)),\n'
_DUE = "    return prior_user_turns > 0 and prior_user_turns % every == 0\n"
_COUNT = '        prior = sum(1 for m in self.history if m.get("role") == "user")\n'
_EXTRA = "        extra = self._build_time_awareness_block() + self._build_behavior_reminder()\n"
_HIST_CHAT = ('        self._attach_turn_blocks(llm_messages)\n\n'
              '        self.history.append({"role": "user", "content": user_message})\n\n'
              '        # ── 沉默闸门 ──\n'
              '        silence_reply = self._should_stay_silent(user_message)\n'
              '        if silence_reply:\n'
              '            self.history.append({"role": "assistant", "content": silence_reply})\n'
              '            self._post_turn(user_message, silence_reply)\n'
              '            return silence_reply\n')
_HIST_STREAM = _HIST_CHAT.replace("            return silence_reply\n",
                                  "            yield silence_reply\n")

# (编号, 靶子测试, [(动作, 文件, 载荷)], 期望)
MUTANTS = [
    # ── 投影 ──
    ("M1  放宽 阶段 k 的做法不进投影", _t("test_p1_phase_k_behaviors_first_then_lifelong"),
     [("repl", VIEW, [(_OWN, "    own = []\n")])], "RED"),
    ("M2  放宽 所有阶段的做法都进投影", _t("test_p4_unlocated_and_other_phases_never_projected"),
     [("repl", VIEW, [(_OWN, "    own = [b for p in card.character_arc.phases for b in p.behaviors]\n")])], "RED"),
    ("M3  放宽 按经历类取阶段 1..k", _t("test_p4_unlocated_and_other_phases_never_projected"),
     [("repl", VIEW, [(_OWN, "    own = [b for p in card.character_arc.phases[:k] for b in p.behaviors]\n")])], "RED"),
    ("M4  放宽 未定位区的做法进投影", _t("test_p4_unlocated_and_other_phases_never_projected"),
     [("repl", VIEW, [(_MERGE, "    proj.situation_behaviors = own + list(proj.situation_behaviors)"
                               " + list(card.character_arc.unlocated.behaviors)\n")])], "RED"),
    ("M5  过严 全程做法被丢", _t("test_p1_phase_k_behaviors_first_then_lifelong"),
     [("repl", VIEW, [(_MERGE, "    proj.situation_behaviors = own\n")])], "RED"),
    ("M6  顺序 全程在前（违反 B3）", _t("test_p1_phase_k_behaviors_first_then_lifelong"),
     [("repl", VIEW, [(_MERGE, "    proj.situation_behaviors = list(proj.situation_behaviors) + own\n")])], "RED"),
    ("M7  原卡被改（不拷贝、就地并入）", _t("test_p5_original_card_untouched"),
     [("repl", VIEW, [(_MERGE, _MERGE + "    card.situation_behaviors[:0] = own\n")])], "RED"),
    # ── 卡片核心层 ──
    ("M8  放宽 不渲染做法块", _t("test_c1_section_lists_phase_and_lifelong_in_order"),
     [("repl", CTX, [(_SECTIONS, "")])], "RED"),
    ("M9  过严 没有做法也出空标题", _t("test_c3_no_behaviors_no_section"),
     [("repl", CTX, [("            if body:\n                out += ", "            if True:\n                out += ")])], "RED"),
    ("M10 位置 做法块挪到语言风格之后", _t("test_c2_section_sits_between_behavior_mode_and_speaking_style"),
     [("repl", CTX, [("        core += self._build_core_sections()\n", ""),
                     ("        core += self._build_phase_block()\n",
                      "        core += self._build_core_sections()\n        core += self._build_phase_block()\n")])], "RED"),
    ("M11 写法 两处各写一份格式（重注入改用别的写法）", _t("test_r2_due_turn_appends_time_then_reminder"),
     [("repl", CHAT, [("behavior_lines(self.card.situation_behaviors)",
                       '"\\n".join(f"- {b.situation}：{b.behavior}" for b in self.card.situation_behaviors)')])], "RED"),
    # ── 重注入 ──
    ("M12 放宽 从不重注入", _t("test_r2_due_turn_appends_time_then_reminder"),
     [("repl", CHAT, [(_DUE, "    return False\n")])], "RED"),
    ("M13 过严 每轮都重注入", _t("test_r3_not_due_turn_only_time"),
     [("repl", CHAT, [(_DUE, "    return prior_user_turns > 0\n")])], "RED"),
    ("M14 过严 间隔改成 2", _t("test_r1_reinject_due_boundaries"),
     [("repl", CHAT, [("REINJECT_EVERY = 4\n", "REINJECT_EVERY = 2\n")])], "RED"),
    ("M15 计数 把角色消息也算成回合", _t("test_r5_greeting_and_assistant_turns_do_not_count"),
     [("repl", CHAT, [(_COUNT, "        prior = len(self.history) // 2\n")])], "RED"),
    ("M16 顺序 提醒排在时间感知之前", _t("test_r2_due_turn_appends_time_then_reminder"),
     [("repl", CHAT, [(_EXTRA, "        extra = self._build_behavior_reminder() + self._build_time_awareness_block()\n")])], "RED"),
    ("M17 泄漏 非流式把带提醒的消息写进历史", _t("test_r7_chat_paths_send_reminder_but_history_keeps_raw_message"),
     [("repl", CHAT, [(_HIST_CHAT, _HIST_CHAT.replace(
         '"content": user_message})\n\n        # ── 沉默', '"content": llm_messages[-1]["content"]})\n\n        # ── 沉默'))])], "RED"),
    ("M18 泄漏 流式把带提醒的消息写进历史", _t("test_r7_chat_paths_send_reminder_but_history_keeps_raw_message"),
     [("repl", CHAT, [(_HIST_STREAM, _HIST_STREAM.replace(
         '"content": user_message})\n\n        # ── 沉默', '"content": llm_messages[-1]["content"]})\n\n        # ── 沉默'))])], "RED"),
    ("M19 无阶段卡 去掉 k>=1 守卫", _t("test_p3_card_without_arc_keeps_only_lifelong"),
     [("repl", VIEW, [(_OWN, "    own = [b.model_copy(deep=True) for b in card.character_arc.phases[k - 1].behaviors]\n")])], "RED"),
    ("M20 接入点 只渲染表里第一项", _t("test_c6_sections_table_is_the_only_entry"),
     [("repl", CTX, [("        for title, body_of in _CORE_SECTIONS:", "        for title, body_of in _CORE_SECTIONS[:1]:")])], "RED"),
    ("M21 过严 没有做法也出提醒标题", _t("test_r4_due_but_no_behaviors_no_reminder"),
     [("repl", CHAT, [("        if not reinject_due(prior) or not self.card.situation_behaviors:",
                       "        if not reinject_due(prior):")])], "RED"),
    # ── 审计补充（spec 补充 1–3）──
    ("M22 过严 只在第一次到期注入（第 9、13 句漏）", _t("test_r9_engine_reinjects_at_turn_5_9_13_only"),
     [("repl", CHAT, [("        if not reinject_due(prior) or not self.card.situation_behaviors:",
                       "        if prior != REINJECT_EVERY or not self.card.situation_behaviors:")])], "RED"),
    ("M23 放宽 群聊也被重注入", _t("test_g1_group_chat_has_section_but_no_reinjection"),
     [("repl", GROUP, [("        system_prompt = engine._compose_context(message)\n"
                        "        # Inject user persona context",
                        "        system_prompt = engine._compose_context(message) + \"【提醒：你遇事的做法】\"\n"
                        "        # Inject user persona context")])], "RED"),
    ("M24 放宽 agent 路径丢了提醒", _t("test_a1_agent_path_carries_section_and_reminder"),
     [("repl", CHAT, [("        return final_sp, llm_messages  # 返回原始 messages（无 tool 消息）",
                       "        return final_sp, [dict(m, content=m['content'].split('\\n\\n【提醒')[0]) for m in llm_messages]")])], "RED"),
    # ── 目标检查（独立预言，四张公版样本卡）：关键变异再打一遍 ──
    ("G-M1 放宽 阶段 k 的做法不进投影", GOAL,
     [("repl", VIEW, [(_OWN, "    own = []\n")])], "RED"),
    ("G-M2 放宽 所有阶段的做法都进投影", GOAL,
     [("repl", VIEW, [(_OWN, "    own = [b for p in card.character_arc.phases for b in p.behaviors]\n")])], "RED"),
    ("G-M8 放宽 不渲染做法块", GOAL,
     [("repl", CTX, [(_SECTIONS, "")])], "RED"),
    ("G-M12 放宽 从不重注入", GOAL,
     [("repl", CHAT, [(_DUE, "    return False\n")])], "RED"),
    ("G-M13 过严 每轮都重注入", GOAL,
     [("repl", CHAT, [(_DUE, "    return prior_user_turns > 0\n")])], "RED"),
    ("G-M17 泄漏 非流式把带提醒的消息写进历史", GOAL,
     [("repl", CHAT, [(_HIST_CHAT, _HIST_CHAT.replace(
         '"content": user_message})\n\n        # ── 沉默', '"content": llm_messages[-1]["content"]})\n\n        # ── 沉默'))])], "RED"),
]


def main() -> int:
    baseline = {p: p.read_bytes() for p in TARGETS}

    print("== 先验基线 ==")
    for lock in (T, GOAL):
        summary, _, _, _ = framework._run(lock)
        print(f"  {lock}  {summary}")
        cause = lock_coverage.baseline_verdict(summary)
        if cause:
            return lock_coverage.refuse_on_baseline({lock: cause})

    problems: list[str] = []
    for label, target, edits, expect in MUTANTS:
        try:
            framework._apply(edits)
            summary, keep, _, _ = framework._run(target)
            outcome = lock_coverage.outcome(summary)
        finally:
            framework._restore(baseline)
        ok = outcome == expect
        if not ok:
            problems.append(f"{label}：实得 {outcome}（期望 {expect}）")
        print(f"\n### {label}   实得={outcome}   {'OK' if ok else 'MISS'}")
        for k in keep[:2]:
            print("   ", k)

    print("\n== 还原核对（sha256 逐字节）==")
    for p in TARGETS:
        same = hashlib.sha256(p.read_bytes()).digest() == hashlib.sha256(baseline[p]).digest()
        if not same:
            problems.append(f"{p.relative_to(ROOT)} 还原后 sha256 不符")
        print(f"  {p.relative_to(ROOT)}  {same}")

    n_red = len(MUTANTS) - len(problems)
    print(f"\n结论：{n_red}/{len(MUTANTS)} 条全红" if not problems else "有问题")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
