# -*- coding: utf-8 -*-
"""B5 / B6（步骤 7）的判别性一次性核实：三条变异各打红一条本步测试，跑完逐字节还原。

用法：在仓库根目录
    .venv/Scripts/python.exe docs/specs/artifacts/arc_phase_fields_b5b6_mutations.py

按 Shiyu 口径，本轮只往常设驱动加 4 条跨模块规则（步骤 1/3/4/6）；步骤 7 的这三条不入常设
驱动，本文件是它们的判别性凭据。框架（改文件 / 跑 pytest / 还原）与判档复用
`tests/perf/mutation_framework.py` 与 `tests/lock_coverage.py`。

退出码：0 = 全红且逐字节还原；1 = 有存活或还原不符。
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

import mutation_framework as framework  # noqa: E402
import lock_coverage  # noqa: E402

EXPORT = ROOT / "core" / "export.py"
AV = ROOT / "core" / "arc_view.py"
CL = ROOT / "core" / "card_layers.py"

U = "tests/test_arc_phase_fields_unit.py"
LOC = "tests/test_arc_phase_fields_locks.py"

MUTANTS = [
    # B6 旧形态：导出先 project_card(card, None)（各阶段 overlay 并进顶层），再按阶段另列一遍
    # → 阶段内容既在全程段又在阶段段。这里直接把各阶段行也拼进全程段，复现同一可见缺陷。
    ("B6 导出把各阶段内容也塞进全程段",
     f"{U}::test_export_phase_content_appears_once_in_its_section",
     [("repl", EXPORT, [("    personality_lines = body(lifelong)\n",
                         "    personality_lines = body(lifelong) + "
                         "[ln for _r in per_phase for ln in body(_r)]\n")])]),
    # B5 旧形态：导出正文写死中文名，不取登记表 label（视图函数在 arc_view.card_outline）。
    ("B5 导出正文写死字段中文名（不取登记表 label）",
     f"{U}::test_export_field_names_come_from_registry",
     [("repl", AV, [("                out.append((path, spec.label, text))",
                     '                out.append((path, "关键记忆", text))')])]),
    # B5 唯一性：登记表两条 label 重复 → S16 红。
    ("B5 登记表两条 label 重复",
     f"{LOC}::test_s16_field_labels_only_in_registry",
     [("repl", CL, [('    "values": FieldSpec("state", "list", "核心价值观"),',
                     '    "values": FieldSpec("state", "list", "内在矛盾"),')])]),
]

TARGETS = [EXPORT, AV, CL]


def main() -> int:
    baseline = {p: p.read_bytes() for p in TARGETS}
    problems = []
    for label, target, edits in MUTANTS:
        try:
            framework._apply(edits)
            summary, keep, _, _ = framework._run(target)
            outcome = lock_coverage.outcome(summary)
        finally:
            framework._restore(baseline)
        ok = outcome == "RED"
        if not ok:
            problems.append(f"{label}：实得 {outcome}")
        print(f"\n### {label}   {outcome}   {'OK' if ok else 'MISS'}")
        for k in keep[:6]:
            print("   ", k)
    print("\n== 还原核对 ==")
    for p in TARGETS:
        same = hashlib.sha256(p.read_bytes()).digest() == hashlib.sha256(baseline[p]).digest()
        if not same:
            problems.append(f"{p.name} 还原不符")
        print(f"  {p.name}  {same}")
    print("\n结论：", "全红" if not problems else problems)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
