"""变异预跑（spec `arc-phase-fields` §7 对账表 MA1–MA23 + 本轮修复 B1–B7 的 MB1…）。

逐条把改后代码改坏一处，对应测试必须红；跑完逐字节还原。

用法：在仓库根目录
    .venv/Scripts/python.exe docs/specs/artifacts/arc_phase_fields_mutations.py

**执行框架与判档不在本文件里**：改文件 / 跑 pytest / 还原用
`tests/perf/mutation_framework.py`（`_apply` 锚点恰一命中、`_run` 取汇总行与断言行、
`_restore`）；判档用 `tests/lock_coverage.py`（`outcome` / `baseline_verdict` /
`refuse_on_baseline`）—— 与仓内各变异驱动共用同一份实现。本文件只放 §7 的变异表。

它**不**登记进覆盖闭合元锁（不写 `*_red_lines.json`、不放 `tests/perf/`）：这是一份 spec
的一次性对账，不是常设守卫 —— 覆盖域是四份新测试，判别器数目远多于 23 条变异能撞到的
集合，闭合在此不可能（同 `arc_phase_select_mutations.py` / `mutate_profile_outbox.py`）。

MA15/MA22/MA23 的靶子是**结构锁**（`test_arc_phase_fields_locks.py` 的 S3 / S11 / S12），
不是 §7 表里写的行为测试：MA15 的 `U15` 只覆盖了导出一半（市场半由 S3 拦），MA22/MA23
的 `S11`/`S12` 原实现对 `core.*` 子模块导入塌成 `core`、且不扫 text_manager —— 已在本段
修正（见 commit message）。见报告里的偏离声明。

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

AV = ROOT / "core" / "arc_view.py"
CL = ROOT / "core" / "card_layers.py"
CD = ROOT / "core" / "card_draft.py"
SCHEMA = ROOT / "core" / "schema.py"
DIST = ROOT / "core" / "distiller.py"
AREV = ROOT / "core" / "moderation" / "auto_review.py"
MARKET = ROOT / "web" / "routers" / "market.py"
RB = ROOT / "core" / "relationship_batch.py"
CTX = ROOT / "core" / "context_engine.py"
TM = ROOT / "core" / "text_manager.py"

TARGETS = (AV, CL, CD, SCHEMA, DIST, AREV, MARKET, RB, CTX, TM)

U = "tests/test_arc_phase_fields_unit.py"
LOC = "tests/test_arc_phase_fields_locks.py"
READERS = "tests/test_arc_phase_fields_readers.py"


def _u(name: str) -> str:
    return f"{U}::{name}"


def _loc(name: str) -> str:
    return f"{LOC}::{name}"


# (编号, 靶子测试, [(动作, 文件, 载荷)], 期望)
MUTANTS = [
    # ── core/arc_view.py：通用投影 ──
    ("MA1 放宽 状态取 1..k 累加", _u("test_state_list_is_top_plus_phase_k"),
     [("repl", AV, [("                base = list(get_path(proj, path) or [])\n"
                     "                set_path(proj, path, base + list(kth or []))",
                     "                base = list(get_path(proj, path) or [])\n"
                     "                set_path(proj, path, base + [x for p in phases[:k]"
                     " for x in (p.overlay.get(path) or [])])")])], "RED"),
    ("MA2 放宽 状态取全部阶段", _u("test_state_list_is_top_plus_phase_k"),
     [("repl", AV, [("                set_path(proj, path, base + list(kth or []))",
                     "                set_path(proj, path, base + [x for p in phases"
                     " for x in (p.overlay.get(path) or [])])")])], "RED"),
    ("MA3 过严 状态丢顶层", _u("test_state_list_is_top_plus_phase_k"),
     [("repl", AV, [("                base = list(get_path(proj, path) or [])\n"
                     "                set_path(proj, path, base + list(kth or []))",
                     "                base = list(get_path(proj, path) or [])\n"
                     "                set_path(proj, path, list(kth or []))")])], "RED"),
    ("MA4 过严 标量阶段空时不回落顶层", _u("test_state_scalar_falls_back_to_top"),
     [("repl", AV, [("            elif kth:", "            elif True:")])], "RED"),
    ("MA5 放宽 经历取 1..k+1", _u("test_experience_takes_1_to_k"),
     [("repl", AV, [("                extra = [x for p in phases[:k] for x in (p.overlay.get(path) or [])]",
                     "                extra = [x for p in phases[:k + 1] for x in (p.overlay.get(path) or [])]")])], "RED"),
    ("MA6 过严 经历只取阶段 k", _u("test_experience_takes_1_to_k"),
     [("repl", AV, [("                extra = [x for p in phases[:k] for x in (p.overlay.get(path) or [])]",
                     "                extra = [x for p in phases[k - 1:k] for x in (p.overlay.get(path) or [])]")])], "RED"),
    ("MA7 改原卡", _u("test_stable_fields_untouched_and_original_not_mutated"),
     [("repl", AV, [("    proj = ProjectedCard(**card.model_dump())", "    proj = card")])], "RED"),
    # ── core/card_layers.py：登记表 ──
    ("MA8 漏注册字段（personality_traits）", _u("test_registry_equals_leaf_set"),
     [("repl", CL, [('    "personality_traits": FieldSpec("state", "list"),\n', "")])], "RED"),
    ("MA8b 漏注册字段（cognitive.knowledge_scope）", _u("test_registry_equals_leaf_set"),
     [("repl", CL, [('    "cognitive.knowledge_scope": FieldSpec("experience", "scalar"),\n', "")])], "RED"),
    ("MA8c 多注册一个不存在的路径", _u("test_registry_equals_leaf_set"),
     [("repl", CL, [('    "character_arc.source_fingerprint": FieldSpec("none", "scalar"),',
                     '    "character_arc.source_fingerprint": FieldSpec("none", "scalar"),\n'
                     '    "nope_field": FieldSpec("stable", "scalar"),')])], "RED"),
    # ── core/card_draft.py：按类别分发 ──
    ("MA9 放宽 experience 挂每个阶段", _u("test_dispatch_experience_hangs_earliest_only"),
     [("repl", CD, [("        return [f if (count and len(f) == count) else f[:1] for f in final_phases]",
                     "        return final_phases")])], "RED"),
    ("MA10 过严 state 只挂最早阶段", _u("test_dispatch_state_hangs_every_valid_phase"),
     [("repl", CD, [('    if layer == "experience":', "    if True:")])], "RED"),
    # ── core/arc_view.py：关系口径 ──
    ("MA11 关系 note 不随阶段", _u("test_relationship_note_follows_phase"),
     [("repl", AV, [("        if latest.note:\n            r2.note = latest.note",
                     "        if latest.note:\n            pass")])], "RED"),
    # ── core/schema.py：起点判定 / 迁移 / overlay 校验 ──
    ("MA12 起点判定不看指纹", _u("test_selectable_and_valid_phase"),
     [("repl", SCHEMA, [("        if not self.phases or not self.source_fingerprint:",
                         "        if not self.phases:")])], "RED"),
    ("MA16 ①格式卡不迁移", _u("test_legacy_phase_fields_migrate_to_overlay"),
     [("repl", SCHEMA, [("            if moved and path not in overlay:", "            if False:")])], "RED"),
    ("MA17 overlay 接受未登记键", _u("test_overlay_rejects_unregistered_key"),
     [("repl", SCHEMA, [('            spec = REGISTRY.get(path)\n'
                         '            if spec is None or spec.layer not in ("state", "experience"):\n'
                         '                raise ValueError(f"overlay 键未登记（须为 state/experience 路径）：{path}")',
                         '            spec = REGISTRY.get(path)\n'
                         '            if spec is None:\n'
                         '                continue\n'
                         '            if spec.layer not in ("state", "experience"):\n'
                         '                raise ValueError(f"overlay 键未登记（须为 state/experience 路径）：{path}")')])], "RED"),
    # ── core/distiller.py：依赖组推导 ──
    ("MA13 依赖组不等 G6", _u("test_phase_dependent_groups_derived"),
     [("repl", DIST, [("    if g != _PHASE_SOURCE_GROUP and _has_phase_leaves(fields)",
                       "    if _has_phase_leaves(fields)")])], "RED"),
    # ── core/moderation/auto_review.py：审核覆盖 overlay ──
    ("MA14 审核跳过 overlay", _u("test_review_covers_overlay"),
     [("repl", AREV, [('    parts = [f"{path}: {text[:500]}" for path, text in iter_texts(card_json)]',
                       '    parts = [f"{path}: {text[:500]}" for path, text in iter_texts(card_json)'
                       ' if ".overlay" not in path]')])], "RED"),
    # ── web/routers/market.py：@ 回复投影（靶子 = S3 结构锁）──
    ("MA15 市场 @ 回复不投影", _loc("test_s3_prompt_builders_go_through_project_card"),
     [("repl", MARKET, [("    char = project_card(char, None)[0]", "    pass  # 变异：不投影")])], "RED"),
    # ── core/relationship_batch.py：切批 / 前缀 ──
    ("MA18 分批丢最后一批", _u("test_relationship_batch_splits_and_merges"),
     [("repl", RB, [("        for i, fut in enumerate(futures):",
                     "        for i, fut in enumerate(futures[:-1]):")])], "RED"),
    ("MA18b 批大小越界", _u("test_relationship_batch_splits_and_merges"),
     [("repl", RB, [("REL_BATCH_SIZE = 10", "REL_BATCH_SIZE = 11")])], "RED"),
    ("MA19 分批走非流式", _loc("test_s9_batches_go_through_collect_stream"),
     [("repl", DIST, [("            reply, truncated = self._collect_stream(\n"
                       '                prompt, messages, "关系生成", "distill_relationships")',
                       "            reply, truncated = self._chat_accounted(\n"
                       '                prompt, messages, "关系生成", "distill_relationships")')])], "RED"),
    ("MA20 分批前缀与主调用不同", _u("test_relationship_batch_prefix_passed_verbatim"),
     [("repl", RB, [("        return list(stream_call(_batch_prompt(prefix, batch, phases), batch) or [])",
                     "        return list(stream_call(_batch_prompt(prefix[:3], batch, phases), batch) or [])")])], "RED"),
    # ── core/context_engine.py：类型隔离 ──
    ("MA21 ContextEngine 收原卡不抛错", _u("test_projected_card_type_only_from_project_card"),
     [("repl", CTX, [("        self.card = require_projected(card)   # 原卡不进注入层（DA18）",
                      "        self.card = card")])], "RED"),
    # ── core/relationship_batch.py：导入方向（靶子 = S11）──
    ("MA22 relationship_batch 导入 distiller", _loc("test_s11_import_direction"),
     [("append", RB, "\nfrom core.distiller import Distiller  # 变异：导入越界\n")], "RED"),
    # ── core/text_manager.py：变体绕过 opening.py（靶子 = S12）──
    ("MA23 新会话开场变体绕过 opening.py 用原卡", _loc("test_s12_opening_prompts_only_in_opening_module"),
     [("repl", TM, [("                from core.arc_view import project_card\n"
                     "                from core.opening import build_variation_prompt\n\n"
                     "                variation_prompt = build_variation_prompt(project_card(card, None)[0])",
                     "                variation_prompt = (\n"
                     '                    f"你是{card.name}。请重新说一句意思相近但措辞不同的开场白。"\n'
                     '                    f"标准开场白：「{card.first_message}」")')])], "RED"),
    # ── 本轮修复（B1–B7）新增的变异条 ────────────────────────────────────────
    # MB1–MB3：B1 分发只有一套 —— 单值字段的格子容量由 `kind` 决定。
    ("MB1 单值不做格子容量限制", _u("test_dispatch_scalar_one_per_slot"),
     [("repl", CD, [('    if kind == "scalar":', "    if False:")])], "RED"),
    ("MB2 单值多余条目提升到顶层（B1 旧缺陷）",
     _u("test_scalar_same_phase_second_dropped_not_promoted"),
     [("repl", CD, [('        by_phase = [_cap_slot(slot, label, f"阶段 {p}") if slot else slot\n'
                     '                    for p, slot in enumerate(by_phase, 1)]',
                     '        for p, slot in enumerate(by_phase, 1):\n'
                     '            if len(slot) > 1:\n'
                     '                logger.warning("[card_draft] %s 的阶段 %d 有多条取值，保留第一条", label, p)\n'
                     '                top = top + slot[1:]\n'
                     '            by_phase[p - 1] = slot[:1]')])], "RED"),
    ("MB3 单值多阶段条目只落最早阶段", _u("test_dispatch_scalar_multi_phase_fills_each_slot"),
     [("repl", CD, [('        by_phase = [_cap_slot(slot, label, f"阶段 {p}") if slot else slot\n'
                     '                    for p, slot in enumerate(by_phase, 1)]',
                     '        seen: set[int] = set()\n'
                     '        for p, slot in enumerate(by_phase, 1):\n'
                     '            kept = [i for i in slot[:1] if i not in seen]\n'
                     '            seen.update(kept)\n'
                     '            by_phase[p - 1] = kept')])], "RED"),
]

BASELINE_TARGETS = (U, READERS, "tests/test_arc_phase_fields_entries.py", LOC)


def main() -> int:
    baseline = {p: p.read_bytes() for p in TARGETS}

    print("== 先验基线 ==")
    for lock in BASELINE_TARGETS:
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
