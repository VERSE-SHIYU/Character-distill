"""变异预跑（spec `arc-phase-select` §6 对账表 M1–M31 + M7b/M11b/M26b，加审计 A1–A3 的 M9b/M10b/M32–M36）。

逐条把改后代码改坏一处，对应测试必须红；跑完逐字节还原。

用法：在仓库根目录
    .venv/Scripts/python.exe docs/specs/artifacts/arc_phase_select_mutations.py

**执行框架与判档不在本文件里**：改文件 / 跑 pytest / 还原用
`tests/perf/mutation_framework.py`（`_apply` 锚点恰一命中、`_run` 取汇总行与断言行、
`_restore`）；判档与基线门用 `tests/lock_coverage.py`（`outcome` / `baseline_verdict` /
`refuse_on_baseline`）—— 与仓内各变异驱动共用同一份实现。本文件只放 §6 的变异表。

它**不**登记进覆盖闭合元锁（不写 `*_red_lines.json`、不放 `tests/perf/`）：这是一份 spec
的一次性对账，不是常设守卫 —— 覆盖域是四份新测试 + 前端用例，判别器数目远多于 34 条变异
能撞到的集合，闭合在此不可能（同 `mutate_profile_outbox.py` 的处置，见 spec §9）。

M18/M19 是前端出口，靶子改为 `npx vitest run`（该文件全部用例），返回码非 0 即 RED。

退出码：0 = 全部红；1 = 有存活；2 = 基线红（拒跑）。
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
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

AV = ROOT / "core" / "arc_view.py"
QUOTES = ROOT / "core" / "quotes.py"
RAG = ROOT / "core" / "rag.py"
ISVC = ROOT / "core" / "indexing_service.py"
SCN = ROOT / "core" / "scene_indexer.py"
SCHEMA = ROOT / "core" / "schema.py"
DIST = ROOT / "core" / "distiller.py"
TM = ROOT / "core" / "text_manager.py"
CTX = ROOT / "core" / "context_engine.py"
CHAT = ROOT / "core" / "chat_engine.py"
EXPORT = ROOT / "core" / "export.py"
SQL = ROOT / "storage" / "sqlite_store.py"
RDIST = ROOT / "web" / "routers" / "distill.py"
STOREJS = ROOT / "web" / "frontend" / "src" / "store" / "useAppStore.js"

TARGETS = (AV, QUOTES, RAG, ISVC, SCN, SCHEMA, DIST, TM, CTX, CHAT, EXPORT, SQL, RDIST, STOREJS)

VIEW = "tests/test_arc_view.py"
POS = "tests/test_arc_positions.py"
SEL = "tests/test_arc_phase_select.py"
JS = "src/store/arcPhase.test.js"


def _v(name: str) -> str:
    return f"{VIEW}::{name}"


def _p(name: str) -> str:
    return f"{POS}::{name}"


def _s(name: str) -> str:
    return f"{SEL}::{name}"


def _run_js() -> tuple[str, list[str], list[str], list[str]]:
    """跑前端那个出口文件（全部用例）；非 0 退出码 = RED。"""
    # 不经 shell（审计 A4）：POSIX 上「列表 + shell=True」只执行 `npx` 本身、其余参数成了
    # shell 的位置参数 —— 挂住或假 RED。`shutil.which` 在 Windows 上解析到 `npx.cmd`。
    npx = shutil.which("npx")
    if npx is None:
        raise SystemExit("找不到 npx：前端变异需要 Node 环境")
    r = subprocess.run(
        [npx, "vitest", "run", JS],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT / "web" / "frontend"))
    out = r.stdout + r.stderr
    keep = [ln.strip()[:300] for ln in out.splitlines()
            if ln.strip().startswith(("FAIL", "×", "AssertionError"))][:6]
    return ("RED" if r.returncode != 0 else lock_coverage.GREEN), keep, [], []


# (编号, 靶子测试, [(动作, 文件, 载荷)], 期望)
MUTANTS = [
    # ── core/arc_view.py：投影 / 归一 / 检索上界 / 关系 / 边界 / 台词 ──
    ("M1  放宽 k<n 时仍注入之后阶段", _v("test_u2_mid_phase_hides_later"),
     [("repl", AV, [("    if k < n:\n        proj.character_arc.phases = [",
                     "    if False:\n        proj.character_arc.phases = [")])], "RED"),
    ("M2  过严 k=n 也加边界说明", _v("test_u2_last_phase_keeps_axis_no_boundary"),
     [("repl", AV, [("    boundary = k < n", "    boundary = True")])], "RED"),
    ("M3  放宽 越界编号原样用", _v("test_u1_phase_number_normalizes"),
     [("repl", AV, [("    if arc_phase is None or not 1 <= arc_phase <= n:",
                     "    if arc_phase is None:")])], "RED"),
    ("M4  过严 下界写成 2", _v("test_u1_phase_number_normalizes"),
     [("repl", AV, [("    if arc_phase is None or not 1 <= arc_phase <= n:",
                     "    if arc_phase is None or not 2 <= arc_phase <= n:")])], "RED"),
    ("M5  放宽 阶段 k+1 的记忆也注入", _v("test_u3_memories_top_plus_upto_k"),
     [("repl", AV, [("[m for p in phases[:k] for m in p.memories]",
                     "[m for p in phases[:k + 1] for m in p.memories]")])], "RED"),
    ("M6  过严 只注入阶段 k 的记忆", _v("test_u3_memories_top_plus_upto_k"),
     [("repl", AV, [("[m for p in phases[:k] for m in p.memories]",
                     "[m for p in phases[k - 1:k] for m in p.memories]")])], "RED"),
    ("M7  放宽 上界用阶段 k+2 起点", _v("test_u4_before_is_next_phase_start"),
     [("repl", AV, [("        before = phases[k].start", "        before = phases[k + 1].start")])], "RED"),
    ("M7b 放宽 不查起点递增", _v("test_u4_non_increasing_gives_none"),
     [("repl", AV, [("    return all(starts[i] < starts[i + 1] for i in range(len(starts) - 1))",
                     "    return True")])], "RED"),
    ("M8  边界 $lt 改 $lte", _p("test_u6_bound_excludes_entries_at_or_after_it"),
     [("repl", RAG, [('            extra = {"where": {"npos": {"$lt": window.before}}}',
                      '            extra = {"where": {"npos": {"$lte": window.before}}}')])], "RED"),
    ("M9  放宽 无位置集合照常检索（裸引擎）", _p("test_u7_bare_engine_missing_pos_schema_returns_empty_and_warns"),
     [("repl", RAG, [("            if (meta.get(POS_SCHEMA_KEY) != POS_SCHEMA\n",
                      "            if False and (meta.get(POS_SCHEMA_KEY) != POS_SCHEMA\n")])], "RED"),
    ("M9b 放宽 不比指纹（裸引擎）", _p("test_u7_bare_engine_fingerprint_mismatch_returns_empty"),
     [("repl", RAG, [("                    or meta.get(FINGERPRINT_KEY) != window.source_fingerprint):",
                      "                    ):")])], "RED"),
    ("M10 过严 指纹相符也返回空", _p("test_u7_bare_engine_matching_fingerprint_filters_by_bound"),
     [("repl", RAG, [("            if (meta.get(POS_SCHEMA_KEY) != POS_SCHEMA\n",
                      "            if True or (meta.get(POS_SCHEMA_KEY) != POS_SCHEMA\n")])], "RED"),
    ("M10b 接线 SessionRag 吞掉 window（不透传）", _p("test_u7_session_rag_unmarked_collection_returns_empty"),
     [("repl", ISVC, [("            return self._engine.query_with_emotion_ex(query_text, **kwargs)",
                       "            kwargs.pop(\"window\", None)\n"
                       "            return self._engine.query_with_emotion_ex(query_text, **kwargs)")])], "RED"),
    ("M11 放宽 场景作业忽略 need_positions（指纹相同就跳过）",
     _p("test_u15_scene_job_rebuilds_when_need_positions_and_no_pos_schema"),
     [("repl", SCN, [("                    if not (need_positions and meta.get(_POS_SCHEMA_KEY) != _POS_SCHEMA_VERSION):",
                      "                    if True:")])], "RED"),
    ("M11b 过严 不带标志的调度也因无 pos_schema 而重建",
     _p("test_u15_scene_job_skips_when_no_flag_even_without_pos_schema"),
     [("repl", SCN, [("                    if not (need_positions and meta.get(_POS_SCHEMA_KEY) != _POS_SCHEMA_VERSION):",
                      "                    if not (meta.get(_POS_SCHEMA_KEY) != _POS_SCHEMA_VERSION):")])], "RED"),
    ("M12 放宽 save_session 覆盖 arc_phase", _s("test_u10_save_session_does_not_clobber_arc_phase"),
     [("repl", SQL, [("                        user_id = excluded.user_id,\n"
                     "                        updated_at = CURRENT_TIMESTAMP",
                     "                        user_id = excluded.user_id,\n"
                     "                        arc_phase = NULL,\n"
                     "                        updated_at = CURRENT_TIMESTAMP")])], "RED"),
    ("M13 接线 /start_session 不写 arc_phase", _s("test_e1_start_session_wires_arc_phase"),
     [("repl", RDIST, [("        await storage.set_session_arc_phase(session_id, user_id, arc_phase)",
                        "        pass  # 变异：不写阶段")])], "RED"),
    ("M14 接线 session_identity 漏 arc_phase", _s("test_e2_rebuild_restores_arc_phase"),
     [("repl", TM, [('    return {"user_role": row.get("user_role") or "", "arc_phase": row.get("arc_phase")}',
                     '    return {"user_role": row.get("user_role") or ""}')])], "RED"),
    ("M15 接线 _scene_items 不传上界", _s("test_m15_scene_items_passes_the_bound_and_fingerprint"),
     [("repl", CTX, [("            window=self.arc_view.window if self.arc_view is not None else None,",
                      "            window=None,")])], "RED"),
    ("M16 接线 记忆留在 G4", _s("test_u9_key_memories_moved_from_g4_to_g6"),
     [("repl", SCHEMA, [('    "G4": ("psyche",),', '    "G4": ("psyche", "key_memories"),'),
                        ('    "G6": ("character_arc", "situation_behaviors", "key_memories"),',
                         '    "G6": ("character_arc", "situation_behaviors"),')])], "RED"),
    ("M17 坐标 用原文坐标", _p("test_u5_normalized_starts_equal_prefix_lengths"),
     [("repl", QUOTES, [("    return [len(normalize(text[:r])) for r in raw_starts]",
                         "    return list(raw_starts)")])], "RED"),
    ("M18 前端 startSessionBody 漏 arc_phase", _run_js,
     [("repl", STOREJS, [("      arc_phase: chosen ?? get().defaultArcPhase(card),\n", "")])], "RED"),
    ("M19 前端 恢复存档用卡片默认值", _run_js,
     [("repl", STOREJS, [("      user_role: session.user_role || get().getUserRole(session.card_id),\n"
                         "      arc_phase: session.arc_phase ?? null,",
                         "      user_role: session.user_role || get().getUserRole(session.card_id),\n"
                         "      arc_phase: null,")])], "RED"),
    ("M20 导出 不导阶段记忆", _s("test_u16_export_includes_phase_memories"),
     [("repl", EXPORT, [("        if phase.memories:                       # 只在那阶段成立的记忆，别从导出里消失",
                         "        if False:")])], "RED"),
    ("M21 坐标 切分后用 text.find 回找起点", _p("test_u16b_split_scenes_start_matches_segment"),
     [("repl", SCN, [("                scenes.append((s + leading_ws(text[s:e]), p))",
                      "                scenes.append((text.find(p), p))")])], "RED"),
    ("M22 复用 存卡调度另写「起点齐全」判定（漏指纹检查）",
     _p("test_u15_card_with_starts_but_no_fingerprint_schedules_neither"),
     [("repl", TM, [("            need_pos = has_positions(card)",
                     "            need_pos = bool(card.character_arc.phases) and all(\n"
                     "                p.start is not None for p in card.character_arc.phases)")])], "RED"),
    ("M23 接线 ChatEngine 用原卡而不是投影卡", _s("test_u21_engine_card_is_the_projected_card"),
     [("repl", CHAT, [("        self.card, self.arc_view = project_card(card, arc_phase)",
                       "        self.arc_view = project_card(card, arc_phase)[1]\n"
                       "        self.card = card")])], "RED"),
    ("M24 放宽 之后才认识的关系也注入", _v("test_u17_mid_phase_takes_latest_upto_k"),
     [("repl", AV, [("        if not upto:\n            continue                               # 之后才认识 → 去掉",
                     "        if not upto:\n            out.append(r.model_copy(deep=True))\n"
                     "            continue                               # 之后才认识 → 去掉")])], "RED"),
    ("M25 取值 取最早而非 ≤k 的最新态度", _v("test_u17_mid_phase_takes_latest_upto_k"),
     [("repl", AV, [("        r2.attitude = max(upto, key=lambda pa: pa.phase).attitude",
                     "        r2.attitude = min(upto, key=lambda pa: pa.phase).attitude")])], "RED"),
    ("M26 放宽 注入所有阶段的边界示范", _v("test_u19_mid_phase_only_own_boundary_examples"),
     [("repl", AV, [("    boundary_examples = list(phases[k - 1].boundary_examples) if boundary else []",
                     "    boundary_examples = [b for p in phases for b in p.boundary_examples] if boundary else []")])], "RED"),
    ("M26b 过严 k=n 也注入边界示范", _v("test_u19_last_phase_no_boundary_examples"),
     [("repl", AV, [("    boundary_examples = list(phases[k - 1].boundary_examples) if boundary else []",
                     "    boundary_examples = list(phases[k - 1].boundary_examples) if True else []")])], "RED"),
    ("M27 接线 G5 与 G6 并行（不等阶段）", _s("test_u20_g5_waits_for_g6_and_others_stay_parallel"),
     [("repl", DIST, [('            if group != "G5":', '            if group != "":')])], "RED"),
    ("M28 边界 落在起点上的台词归前一阶段", _v("test_u18_phase_of_boundary_is_later_phase"),
     [("repl", AV, [("        if s is not None and s <= pos:", "        if s is not None and s < pos:")])], "RED"),
    ("M29 取值 k=n 用阶段态度而非顶层态度", _v("test_u17_last_phase_uses_top_attitude"),
     [("repl", AV, [("    if k == n:\n        return [r.model_copy(deep=True) for r in rels]",
                     "    if False:\n        return [r.model_copy(deep=True) for r in rels]")])], "RED"),
    ("M30 放宽 k<n 仍用全书开场白", _v("test_u19b_mid_phase_clears_first_message"),
     [("repl", AV, [('        proj.first_message = ""\n', "")])], "RED"),
    ("M31 放宽 之后阶段的台词也注入", _v("test_u18_dialogues_top_plus_upto_k"),
     [("repl", AV, [("        d for p in card.character_arc.phases[:k] for d in p.dialogue_examples",
                     "        d for p in card.character_arc.phases for d in p.dialogue_examples")])], "RED"),
    # ── 审计 A2：text_ 补位置的判断在后台作业里（两个方向） ──
    ("M32 放宽 补位置作业不看 pos_schema（已有位置也重建）",
     _p("test_a2_reposition_leaves_positioned_collection_alone"),
     [("repl", ISVC, [("        if (rag.collection.metadata or {}).get(POS_SCHEMA_KEY) == POS_SCHEMA:\n            return rag\n", "")])], "RED"),
    ("M33 过严 补位置作业缺位置也不重建",
     _p("test_a2_reposition_rebuilds_only_when_positions_missing"),
     [("repl", ISVC, [("        if (rag.collection.metadata or {}).get(POS_SCHEMA_KEY) == POS_SCHEMA:\n            return rag\n",
                       "        return rag\n")])], "RED"),
    ("M34 放宽 集合不存在也整本新建（与场景作业抢建）",
     _p("test_a2_reposition_skips_missing_collection"),
     [("repl", ISVC, [('        if not rag.load_existing(f"text_{text_id}"):\n            return None\n',
                       '        rag.load_existing(f"text_{text_id}")\n'
                       '        if rag.collection is None:\n'
                       '            return self._build_text_collection(\n'
                       '                text_id, text, all_characters,\n'
                       '                embedding_key=embedding_key, embedding_region=embedding_region, rebuild=rebuild)\n')])], "RED"),
    ("M35 接线 存卡调度不带 only_if_missing_positions（无条件重建）",
     _p("test_u15_card_with_positions_schedules_need_positions_and_reposition"),
     [("repl", TM, [("                    only_if_missing_positions=True,\n", "")])], "RED"),
    # ── 审计 A3：阶段号范围规则只在 arc_view ──
    ("M36 放宽 valid_phase 不查上界（越界编号原样存库）", _v("test_u1_phase_number_normalizes"),
     [("repl", AV, [("    if arc_phase is None or not 1 <= arc_phase <= n:",
                     "    if arc_phase is None or not 1 <= arc_phase:")])], "RED"),
]


def main() -> int:
    baseline = {p: p.read_bytes() for p in TARGETS}

    print("== 先验基线 ==")
    for lock in (VIEW, POS, SEL):
        summary, _, _, _ = framework._run(lock)
        print(f"  {lock}  {summary}")
        cause = lock_coverage.baseline_verdict(summary)
        if cause:
            return lock_coverage.refuse_on_baseline({lock: cause})
    got, _, _, _ = _run_js()
    print(f"  前端 {JS}  {got}")
    if got != lock_coverage.GREEN:
        return lock_coverage.refuse_on_baseline({JS: "前端基线不绿"})

    problems: list[str] = []
    for label, target, edits, expect in MUTANTS:
        try:
            framework._apply(edits)
            if callable(target):
                outcome, keep, _, _ = target()
            else:
                summary, keep, _, _ = framework._run(target)
                outcome = lock_coverage.outcome(summary)
        finally:
            framework._restore(baseline)
        ok = outcome == expect
        if not ok:
            problems.append(f"{label}：实得 {outcome}（期望 {expect}）")
        print(f"\n### {label}   实得={outcome}   {'OK' if ok else 'MISS'}")
        for k in keep[:6]:
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
