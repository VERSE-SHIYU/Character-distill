# -*- coding: utf-8 -*-
"""锚点锁：每个常设变异驱动（`tests/perf/*_mutations.py`）的每个锚点，在**当前代码**上恰好命中一次。

**它防的是什么。** 元锁 `test_lock_coverage.py` 只核对驱动跑出来的产物（`*_red_lines.json`），
不核对驱动本身在当前代码上还跑不跑得通。代码一改，锚点对不上，驱动就悄悄失效 —— 直到下一次
有人手跑才发现（实例：#116 之后 `arc_phase_anchoring_mutations.py` 的 M17 / M30 在 main
`c97116b0` 上命中 0 次 / 3 次，`card_draft_mutations.py` 的 M27 滑到了同形的另一个分支上，
元锁照绿；spec `arc-phase-unlocated.md` §8）。

**怎么核。** 只加载驱动、不跑变异：按驱动自己的变异表（`GROUPS`，没有就 `MUTATIONS`），把每条
变异的编辑在**内存里**依次套到文件文本上，判据与执行原语 `mutation_framework._apply` 同一条
（`repl` 的锚点恰一命中；`append` / `hide` 的文件存在；`write` 的文件不存在）。几秒跑完，进 CI。
同一条变异里的多处 `repl` 依次套（后一处的锚点可能落在前一处换进去的文本上），不同变异互不影响。

它管不到「锚点恰一命中、却落在了没有测试的同形分支上」（M27 那种）—— 那由驱动跑出的
「空转变异」判定（元锁）管；本锁只保证驱动还能跑起来。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PERF = ROOT / "tests" / "perf"
sys.path.insert(0, str(PERF))

DRIVERS = sorted(PERF.glob("*_mutations.py"))


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"_anchor_lock_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 驱动的 main 在 `if __name__ == "__main__"` 下，不会跑
    return mod


def _mutations(mod) -> list:
    groups = getattr(mod, "GROUPS", None)
    if groups:
        return [m for ms in groups.values() for m in ms]
    return list(getattr(mod, "MUTATIONS"))


def _problems(mutations) -> list[str]:
    out: list[str] = []
    for item in mutations:                # `(label, target, edits, expect[, marker])`，同 run_matrix
        label, _target, edits, _expect = item[:4]
        texts: dict[pathlib.Path, str] = {}
        for kind, path, payload in edits:
            path = pathlib.Path(path)
            if kind == "write":
                if path.exists():
                    out.append(f"{label}：write 的目标已存在 {path.name}")
                continue
            if not path.exists():
                out.append(f"{label}：{kind} 的目标不存在 {path}")
                continue
            if kind != "repl":
                continue
            src = texts.setdefault(path, path.read_text(encoding="utf-8"))
            for old, new in payload:
                hits = src.count(old)
                if hits != 1:
                    out.append(f"{label}：锚点在 {path.name} 命中 {hits} 次（应恰 1）：{old[:60]!r}")
                    break
                src = src.replace(old, new)
            texts[path] = src
    return out


def test_there_are_standing_drivers_to_check():
    """非空守卫：扫描面塌了的话，下面的参数化一条都不跑、恒绿。"""
    assert len(DRIVERS) >= 7


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda p: p.name)
def test_every_anchor_hits_exactly_once_on_current_code(driver):
    assert _problems(_mutations(_load(driver))) == []


def test_lock_sees_a_drifted_anchor():
    """正控：锚点漂移（命中 0 次、多次）必须被报出来，否则这把锁空转。"""
    me = pathlib.Path(__file__)
    fake = [("D0 命中 0 次", "", [("repl", me, [("这句话不在任何文件里" * 2, "x")])], "RED"),
            ("D2 命中多次", "", [("repl", me, [("import ", "x")])], "RED"),
            ("D1 恰一次", "", [("repl", me, [("def test_lock_sees_a_" + "drifted_anchor", "x")])], "RED")]
    assert [p.split("：")[0] for p in _problems(fake)] == ["D0 命中 0 次", "D2 命中多次"]
