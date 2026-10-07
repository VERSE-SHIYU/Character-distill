"""审计第三轮自补变异（spec `arc-phase-unlocated.md` 补充 15–18、补充 17 的 400 文案拆分）。

不在 §7 对账表与 `arc_phase_unlocated_audit_mutations.py` 里的放宽 / 过严各若干条。一次性产物，
不登记进元锁。

**执行框架与判档不在本文件里**（补充 20 的修法）：改文件、跑 pytest、按字节还原用
`tests/perf/mutation_framework.py`，判档与基线门用 `tests/lock_coverage.py` —— 与
`arc_phase_unlocated_audit_mutations.py` 同一处置。首版自己写了 subprocess 与 write_text，
在中文 Windows 上解码崩、还原把 LF 翻成 CRLF、也没有基线门。

用法：仓库根目录 `python docs/specs/artifacts/arc_phase_unlocated_audit3_mutations.py`。
期望：全部 RED。R2、R3、R4、S1 在补用例前存活（= 补充 15–18）；R5、R6、S4 守补充 17。
退出码：0 = 全部符合预期；1 = 有存活；2 = 基线红（拒跑）。
"""
from __future__ import annotations

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

SCHEMA = ROOT / "core" / "schema.py"
LAYERS = ROOT / "core" / "card_layers.py"
MOVE = ROOT / "core" / "unlocated.py"
RDIST = ROOT / "web" / "routers" / "distill.py"
FE = ROOT / "web" / "frontend"
UNLJ = FE / "src" / "components" / "common" / "UnlocatedList.jsx"
TARGETS = (SCHEMA, LAYERS, MOVE, RDIST, UNLJ)

JS = ("src/components/__tests__/UnlocatedList.test.jsx",
      "src/components/__tests__/CharCardUnlocated.test.jsx")

UNL = "tests/test_arc_phase_unlocated.py"
MV = "tests/test_arc_phase_unlocated_move.py"


_run_js = framework.vitest(*JS)   # 前端靶子：跑法在框架里（补充 20 的根因修法）


def _repl(path: pathlib.Path, old: str, new: str) -> list:
    return [("repl", path, [(old, new)])]


# (编号, 靶子, [(动作, 文件, 载荷)], 期望)
MUTANTS = [
    ("R1 放宽 未定位区 overlay 不要求一律列表（单值混进来）", UNL,
     _repl(SCHEMA, 'layers=("state",), lists_only=True,', 'layers=("state",), lists_only=False,'), "RED"),
    ("R2 放宽 未定位区 overlay 收经历类路径（补充 15）", f"{UNL}::test_n4c_unlocated_overlay_rejects_experience_path",
     _repl(SCHEMA, 'layers=("state",), lists_only=True,',
           'layers=("state", "experience"), lists_only=True,'), "RED"),
    ("R3 放宽 交换退回的态度丢掉原阶段号（补充 16）", f"{MV}::test_m6_attitude_into_phase_with_attitude_swaps_back",
     _repl(MOVE, '"note": same["note"], "phase": phase})', '"note": same["note"], "phase": 0})'), "RED"),
    ("R4 放宽 态度默认阶段不查上界（补充 17 前端）", _run_js,
     _repl(UNLJ, "initial: a.phase >= 1 && a.phase <= last ? a.phase : last,",
           "initial: a.phase >= 1 ? a.phase : last,"), "RED"),
    ("S1 过严 单阶段卡不显示未定位区（补充 18）", _run_js,
     _repl(UNLJ, "if (!last || !hasUnlocated(arc)) return null",
           "if (last < 2 || !hasUnlocated(arc)) return null"), "RED"),
    ("S2 过严 overlay 校验拒空列表（引文核对清空后的卡读不回来）", UNL,
     _repl(LAYERS, '            raise ValueError(f"{where}[{path}] 应为列表")\n',
           '            raise ValueError(f"{where}[{path}] 应为列表")\n'
           '        if isinstance(val, list) and not val:\n'
           '            raise ValueError(f"{where}[{path}] 不许为空")\n'), "RED"),
    ("S3 过严 不许挪进最后阶段", MV,
     _repl(MOVE, "    if not 1 <= phase <= n:", "    if not 1 <= phase < n:"), "RED"),
    # ── 补充 17 的 400 文案拆分，两侧 ──
    ("R5 放宽 阶段越界与其他参数错共用一句文案（路由不分）",
     f"{MV}::test_route_phase_out_of_range_is_400_with_its_own_message",
     _repl(RDIST, "    except UnknownPhase as exc:", "    except ArithmeticError as exc:"), "RED"),
    ("R6 放宽 越界仍抛普通 ValueError（分类在源头丢掉）", f"{MV}::test_m9_only_phase_out_of_range_is_unknown_phase",
     _repl(MOVE, '        raise UnknownPhase(f"阶段号越界', '        raise ValueError(f"阶段号越界'), "RED"),
    ("S4 过严 所有参数错都报「阶段已不存在」", f"{MV}::test_route_stale_index_is_400",
     _repl(RDIST, "    except UnknownPhase as exc:", "    except ValueError as exc:"), "RED"),
]


def main() -> int:
    mutants = MUTANTS
    return framework.run_oneoff(mutants, targets=TARGETS, gates=(UNL, MV, _run_js))


if __name__ == "__main__":
    sys.exit(main())
