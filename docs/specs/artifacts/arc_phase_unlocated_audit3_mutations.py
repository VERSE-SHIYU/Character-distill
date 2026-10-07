"""审计第三轮自补变异（spec `arc-phase-unlocated.md` 补充 15–18）：不在 §7 对账表与
`arc_phase_unlocated_audit_mutations.py` 里的放宽 / 过严各若干条。一次性产物，不登记进元锁。

用法：仓库根目录 `python docs/specs/artifacts/arc_phase_unlocated_audit3_mutations.py`。
期望：R1、S2、S3 红；R2、R3、R4、S1 在补用例前存活（= 补充 15–18），补后应全红。
"""
import pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
FE = ROOT / "web/frontend"
PY = sys.executable
BACK = ["tests/test_arc_phase_unlocated.py", "tests/test_arc_phase_unlocated_goal.py",
        "tests/test_arc_phase_unlocated_move.py", "tests/test_phase_anchoring.py",
        "tests/test_card_quotes.py", "tests/test_card_guard.py", "tests/test_card_optimistic_lock.py",
        "tests/test_arc_phase_fields_unit.py", "tests/test_arc_phase_fields_readers.py",
        "tests/test_arc_phase_fields_locks.py", "tests/test_card_arc_behaviors.py"]
FRONT = ["src/components/__tests__/UnlocatedList.test.jsx",
         "src/components/__tests__/CharCardUnlocated.test.jsx"]

M = [
    ("R1 放宽 未定位区 overlay 不要求一律列表（单值混进来）", "core/schema.py",
     'layers=("state",), lists_only=True,', 'layers=("state",), lists_only=False,', "py"),
    ("R2 放宽 未定位区 overlay 收经历类路径（key_memories 可进未定位区）", "core/schema.py",
     'layers=("state",), lists_only=True,', 'layers=("state", "experience"), lists_only=True,', "py"),
    ("R3 放宽 交换退回的态度丢掉原阶段号（挪回时预选失效）", "core/unlocated.py",
     '"note": same["note"], "phase": phase})', '"note": same["note"], "phase": 0})', "py"),
    ("R4 放宽 态度默认阶段不查上界（阶段被删后预选值不在选项里）", "web/frontend/src/components/common/UnlocatedList.jsx",
     "initial: a.phase >= 1 && a.phase <= last ? a.phase : last,", "initial: a.phase >= 1 ? a.phase : last,", "js"),
    ("S1 过严 单阶段卡不显示未定位区", "web/frontend/src/components/common/UnlocatedList.jsx",
     "if (!last || !hasUnlocated(arc)) return null", "if (last < 2 || !hasUnlocated(arc)) return null", "js"),
    ("S2 过严 overlay 校验拒空列表（引文核对清空后的卡读不回来）", "core/card_layers.py",
     '            raise ValueError(f"{where}[{path}] 应为列表")\n',
     '            raise ValueError(f"{where}[{path}] 应为列表")\n'
     '        if isinstance(val, list) and not val:\n'
     '            raise ValueError(f"{where}[{path}] 不许为空")\n', "py"),
    ("S3 过严 不许挪进最后阶段", "core/unlocated.py",
     "    if not 1 <= phase <= n:", "    if not 1 <= phase < n:", "py"),
]


def run(kind):
    if kind == "py":
        r = subprocess.run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *BACK,
                            "--deselect", "tests/test_arc_phase_fields_unit.py::test_relationship_batch_splits_and_merges"],
                           cwd=ROOT, capture_output=True, text=True)
    else:
        r = subprocess.run([shutil.which("npx") or "npx", "vitest", "run", *FRONT], cwd=FE, capture_output=True, text=True)
    tail = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-1]
    return ("RED" if r.returncode else "GREEN(存活)"), tail


print("基线:", run("py"), run("js"))
for label, rel, old, new, kind in M:
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"{label}: 锚点命中 {src.count(old)} 次"
    try:
        p.write_text(src.replace(old, new), encoding="utf-8")
        outcome, tail = run(kind)
    finally:
        p.write_text(src, encoding="utf-8")
    print(f"### {label}   实得={outcome}\n    {tail[:160]}")
