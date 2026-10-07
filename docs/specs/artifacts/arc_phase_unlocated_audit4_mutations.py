# -*- coding: utf-8 -*-
"""审计第四轮（独立复核）自补变异：放宽 2 条 + 过严 2 条，靶子都在本次改动面内
（补充 17 的 `UnknownPhase` 分类与 400 文案拆分、补充 17/18 的前端预选与渲染门）。

与 `arc_phase_unlocated_audit3_mutations.py` 同处置：一次性产物，不登记进元锁。
与它两点不同，本脚本自带：① 子进程解码显式 `encoding="utf-8"`（否则中文测试输出在
GBK 语言环境下会让读线程崩、`r.stdout` 变 None）；② 跑前先验基线，基线不绿直接拒跑，
免得把「本来就红」当成「变异打红了」。

用法：仓库根目录 `python docs/specs/artifacts/arc_phase_unlocated_audit4_mutations.py`。
期望：全部红。存活 = 一条发现（写进 spec 补充）。
"""
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
    # ── 放宽：把守卫改弱 ──
    ("T1 放宽 路由先接宽 except（子类分支不可达，越界退回通用文案）", "web/routers/distill.py",
     "    except UnknownPhase as exc:                  # 补充 17：阶段号越界单独报，不说「不在未定位区」\n"
     "        logger.warning(\"[distill] move_unlocated rejected: %s\", exc)\n"
     "        raise HTTPException(400, \"所选的阶段已不存在，请刷新后重新选择\") from exc\n"
     "    except ValueError as exc:\n"
     "        logger.warning(\"[distill] move_unlocated rejected: %s\", exc)\n"
     "        raise HTTPException(400, \"这一条已经不在未定位区，请刷新后重试\") from exc\n",
     "    except ValueError as exc:\n"
     "        logger.warning(\"[distill] move_unlocated rejected: %s\", exc)\n"
     "        raise HTTPException(400, \"这一条已经不在未定位区，请刷新后重试\") from exc\n"
     "    except UnknownPhase as exc:\n"
     "        logger.warning(\"[distill] move_unlocated rejected: %s\", exc)\n"
     "        raise HTTPException(400, \"所选的阶段已不存在，请刷新后重新选择\") from exc\n", "py"),
    ("T2 放宽 序号错也抛 UnknownPhase（越界分类放宽到非越界错）", "core/unlocated.py",
     '        raise ValueError(f"未定位区没有这一条{what}：{index}")',
     '        raise UnknownPhase(f"未定位区没有这一条{what}：{index}")', "py"),
    # ── 过严：把正常路径改拒 ──
    ("T3 过严 序号上界收紧一格（列表最后一条挪不动）", "core/unlocated.py",
     "    if not 0 <= index < len(items):",
     "    if not 0 <= index < len(items) - 1:", "py"),
    ("T4 过严 渲染门要求两条以上（只剩一类条目的卡整块不渲染）",
     "web/frontend/src/components/common/UnlocatedList.jsx",
     "    + overlayLeaves(u.overlay).reduce((n, [, v]) => n + (v?.length || 0), 0) > 0",
     "    + overlayLeaves(u.overlay).reduce((n, [, v]) => n + (v?.length || 0), 0) > 1", "js"),
]


def run(kind):
    if kind == "py":
        cmd = [PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *BACK,
               "--deselect", "tests/test_arc_phase_fields_unit.py::test_relationship_batch_splits_and_merges"]
        cwd = ROOT
    else:
        cmd = [shutil.which("npx") or "npx", "vitest", "run", *FRONT]
        cwd = FE
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    lines = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
    return ("RED" if r.returncode else "GREEN(存活)"), (lines[-1] if lines else "<no output>")


base_py, base_js = run("py"), run("js")
print(f"基线: {base_py} {base_js}")
if "GREEN" not in base_py[0] or "GREEN" not in base_js[0]:
    print("基线不绿 → 拒跑")
    raise SystemExit(2)

for label, rel, old, new, kind in M:
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"{label}: 锚点命中 {src.count(old)} 次"
    try:
        p.write_text(src.replace(old, new), encoding="utf-8", newline="")
        outcome, tail = run(kind)
    finally:
        p.write_text(src, encoding="utf-8", newline="")     # newline="" 防 Windows 把 LF 翻成 CRLF
    print(f"### {label}   实得={outcome}\n    {tail[:160]}")
