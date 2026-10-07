# -*- coding: utf-8 -*-
"""审计第四轮（独立复核）自补变异：放宽 2 条 + 过严 2 条，靶子都在本次改动面内
（补充 17 的 `UnknownPhase` 分类与 400 文案拆分、补充 17/18 的前端预选与渲染门）。

一次性产物，不登记进元锁。跑法（基线门、施加、按字节还原、UTF-8 解码、判档）全在
`tests/perf/mutation_framework.py` 的 `run_oneoff`；本文件只有变异表。四条的锚点与改法与
执行方原版逐字相同，只把靶子从「整组测试」收窄到守它的那个文件。

用法：仓库根目录 `python docs/specs/artifacts/arc_phase_unlocated_audit4_mutations.py`。
期望：全部红。存活 = 一条发现（写进 spec 补充）。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "perf"))

import mutation_framework as framework  # noqa: E402

MV = "tests/test_arc_phase_unlocated_move.py"
_run_js = framework.vitest("src/components/__tests__/UnlocatedList.test.jsx",
                           "src/components/__tests__/CharCardUnlocated.test.jsx")
_TARGET = {"web/routers/distill.py": MV, "core/unlocated.py": MV}   # 后端变异 → 守它的文件

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

MUTANTS = [(label, _run_js if kind == "js" else _TARGET[rel],
            [("repl", ROOT / rel, [(old, new)])], "RED")
           for label, rel, old, new, kind in M]
TARGETS = tuple(sorted({ROOT / rel for _l, rel, _o, _n, _k in M}))


def main() -> int:
    return framework.run_oneoff(MUTANTS, targets=TARGETS, gates=(MV, _run_js))


if __name__ == "__main__":
    sys.exit(main())
